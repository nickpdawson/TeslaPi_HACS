"""Config entry migrations for TeslaPi."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_EXTRA_HOSTS,
    CONF_HOST,
    DOMAIN,
    LEGACY_ENTITY_KEYS,
    LOGGER,
    REMOVED_ENTITY_KEYS,
)
from .coordinator import extra_hosts_from_options


def _legacy_key(domain: str, unique_id: str) -> str | None:
    """Return the entity key from a version 1 unique ID, "<host>_<key>"."""
    for key in sorted(LEGACY_ENTITY_KEYS.get(domain, ()), key=len, reverse=True):
        if unique_id.endswith(f"_{key}") and len(unique_id) > len(key) + 1:
            return key
    return None


@callback
def migrate_v1_to_v2(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-key entities and the device on the entry ID instead of the host.

    Registry entries are updated in place, so entity IDs, names, areas and
    recorder history stay attached. Version 1 used whichever host answered
    first, so one entry can hold IDs for several hosts; IDs for the
    configured primary host win, and any duplicates are removed.
    """
    primary = entry.data.get(CONF_HOST, "")
    new_prefix = f"{entry.entry_id}_"

    ent_reg = er.async_get(hass)
    reg_entries = [
        e
        for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
        if not e.unique_id.startswith(new_prefix)
    ]
    reg_entries.sort(key=lambda e: not e.unique_id.startswith(f"{primary}_"))

    for reg_entry in reg_entries:
        key = _legacy_key(reg_entry.domain, reg_entry.unique_id)
        if key is None:
            continue
        if (reg_entry.domain, key) in REMOVED_ENTITY_KEYS:
            LOGGER.info("Removing duplicate entity %s", reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)
            continue
        new_unique_id = f"{new_prefix}{key}"
        if ent_reg.async_get_entity_id(reg_entry.domain, DOMAIN, new_unique_id):
            LOGGER.info("Removing duplicate entity %s", reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)
            continue
        LOGGER.debug(
            "Migrating %s unique_id %s -> %s",
            reg_entry.entity_id,
            reg_entry.unique_id,
            new_unique_id,
        )
        ent_reg.async_update_entity(reg_entry.entity_id, new_unique_id=new_unique_id)

    dev_reg = dr.async_get(hass)
    new_identifier = (DOMAIN, entry.entry_id)
    devices = [
        d
        for d in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        if any(i[0] == DOMAIN for i in d.identifiers)
    ]
    devices.sort(key=lambda d: (DOMAIN, primary) not in d.identifiers)
    keeper = next((d for d in devices if new_identifier in d.identifiers), None)
    for device in devices:
        if device is keeper:
            continue
        if keeper is None:
            others = {i for i in device.identifiers if i[0] != DOMAIN}
            dev_reg.async_update_device(
                device.id, new_identifiers=others | {new_identifier}
            )
            keeper = device
            continue
        # A second device for the same Pi, left by a fallback host. Move its
        # entities to the kept device and drop it.
        for reg_entry in er.async_entries_for_device(
            ent_reg, device.id, include_disabled_entities=True
        ):
            ent_reg.async_update_entity(reg_entry.entity_id, device_id=keeper.id)
        dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    options = {
        **entry.options,
        CONF_EXTRA_HOSTS: extra_hosts_from_options(dict(entry.options)),
    }
    hass.config_entries.async_update_entry(entry, options=options, version=2)
