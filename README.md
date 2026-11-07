# BRU Tea Maker for Home Assistant

Control a **BRU Maker One** (model BRU 100) tea maker from Home Assistant over
Bluetooth. You can see what the machine is doing, choose the temperature, water
amount and brew time, and start a brew. Everything runs locally; no cloud
account is involved.

## Requirements

- Home Assistant 2026.10 or newer.
- A Bluetooth adapter on the Home Assistant machine, within range of the tea
  maker, **or** an ESPHome Bluetooth proxy near the tea maker with the extra
  configuration described [below](#esphome-bluetooth-proxy).

## Installation

### HACS

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=girtskokars&repository=ha-bru-tea-maker&category=integration)

1. Click the button above, or open HACS, choose **Custom repositories** from
   the menu in the top right corner, and add
   `https://github.com/girtskokars/ha-bru-tea-maker` as an **Integration**.
2. Download **BRU Tea Maker**.
3. Restart Home Assistant.

### Manual

1. Copy the `custom_components/bru_tea_maker` folder into the
   `custom_components` folder of your Home Assistant configuration.
2. Restart Home Assistant.

## Setup

[![Add the integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=bru_tea_maker)

1. On the tea maker, open **Machine setup → Pair new device** and press the
   main button. Bluetooth must be on (**Machine setup → Bluetooth**).
2. In Home Assistant, go to **Settings → Devices & services**. The tea maker is
   usually discovered on its own; otherwise choose **Add integration** and
   search for **BRU Tea Maker**.
3. Home Assistant shows a 6-digit code, and the tea maker shows the same one.
   Press **yes** on the tea maker, then **Submit** in Home Assistant.

Pairing is only needed once.

## Entities

| Entity | Type | What it does |
| --- | --- | --- |
| *(device name)* | Sensor | What the tea maker is doing: standby, filling, steeping, serving, and so on. It is the device's main entity, so it is shown with the device name alone. |
| Display message | Sensor | The message on the tea maker's display, such as "Place cup" or "Add water". |
| Brewing | Binary sensor | On while a brew is running. |
| Needs attention | Binary sensor | On while the tea maker needs someone before it can brew: a prompt such as "Place cup", "Close lid" or "Add water", or an error. The "Open lid" reminder after a brew does not count. |
| Brew progress | Event | Fires **Started** when a brew starts and **Finished** when the tea is ready, once per brew. |
| Water temperature | Number | Water temperature for the next brew: 40–100 °C, in steps of 5. The lowest setting, 40 °C, is what the tea maker shows as **Cold**: the water is not heated. |
| Water quantity | Number | Amount of water for the next brew: 50–500 ml, in steps of 50. |
| Steeping time | Number | Steeping time for the next brew: 10–600 seconds, in steps of 10. |
| Preset | Select | Copies one of the tea maker's own presets into the three settings above. Shows **Custom** once a setting is changed by hand. Presets with the same name get the slot number added, such as "GREEN TEA (2)". Unavailable until the presets have been read. |
| Brew | Button | Starts a brew with the current settings. If the tea maker is busy or shows a prompt such as "Place cup", it says so instead. |
| Sync presets | Button | Reads the presets from the tea maker again, after you change them on the machine. |

The brew settings live in Home Assistant, so they can be changed even while
the tea maker is out of range. A value between steps, such as 92 °C, is
rounded to the nearest one the tea maker accepts.

The device page shows the tea maker's firmware version, and as its serial
number the end of its Bluetooth name (`1422E73F` for `BRU.M2-1422E73F`), to
tell two tea makers apart.

The examples below use the entity IDs of a new installation, such as
`number.bru_tea_maker_water_temperature`. Installations from before 1.1.0 keep
their earlier IDs, such as `number.bru_tea_maker_temperature`; check yours
under **Settings → Entities**.

## Dashboard

A card with the status, the settings and a Brew button:

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: sensor.bru_tea_maker
  - type: tile
    entity: select.bru_tea_maker_preset
    features:
      - type: select-options
  - type: tile
    entity: number.bru_tea_maker_water_temperature
    features:
      - type: numeric-input
        style: buttons
  - type: tile
    entity: number.bru_tea_maker_water_quantity
    features:
      - type: numeric-input
        style: buttons
  - type: tile
    entity: number.bru_tea_maker_steeping_time
    features:
      - type: numeric-input
        style: buttons
  - type: tile
    entity: button.bru_tea_maker_brew
    tap_action:
      action: perform-action
      perform_action: button.press
      target:
        entity_id: button.bru_tea_maker_brew
```

## Actions

**BRU Tea Maker: Brew** (`bru_tea_maker.brew`) starts a brew in one step,
without changing the settings shown in Home Assistant. It uses a preset, or
the current settings when no preset is given, and any of the values given
override it.

```yaml
automation:
  - alias: Morning tea
    triggers:
      - trigger: time
        at: "07:00:00"
    actions:
      - action: bru_tea_maker.brew
        data:
          device_id: YOUR_DEVICE_ID
          preset: GREEN TEA
          water_amount: 300
```

| Field | Description |
| --- | --- |
| `device_id` | The tea maker. Required. |
| `preset` | Name of one of the tea maker's presets, as shown in the Preset select. |
| `temperature` | Water temperature, 40–100 °C. 40 °C is **Cold**. |
| `water_amount` | Water quantity, 50–500 ml. |
| `brew_time` | Steeping time, 10–600 seconds. |

## Automation examples

Notify when the tea is ready:

```yaml
automation:
  - alias: Tea is ready
    triggers:
      - trigger: state
        entity_id: event.bru_tea_maker_brew_progress
        not_from: [unavailable, unknown]
        not_to: unavailable
    conditions:
      - condition: state
        entity_id: event.bru_tea_maker_brew_progress
        attribute: event_type
        state: finished
    actions:
      - action: notify.mobile_app_your_phone
        data:
          message: "{{ device_name('event.bru_tea_maker_brew_progress') }}: your tea is ready."
```

Notify when the tea maker needs you, with the message from its display:

```yaml
automation:
  - alias: Tea maker needs attention
    triggers:
      - trigger: state
        entity_id: binary_sensor.bru_tea_maker_needs_attention
        from: "off"
        to: "on"
    actions:
      - action: notify.mobile_app_your_phone
        data:
          message: >-
            {{ device_name('binary_sensor.bru_tea_maker_needs_attention') }}:
            {{ state_translated('sensor.bru_tea_maker_display_message') }}
```

Use `device_name()` and `entity_name()` for names in templates, not the
`friendly_name` attribute, so they follow what the tea maker is called in Home
Assistant.

## Troubleshooting

- **Pairing failed.** The setup dialog says what went wrong and lets you try
  again. After a failed attempt the tea maker may refuse new ones for a few
  minutes; the dialog then suggests how long to wait.
- **The tea maker is not found.** Setup lists what to check and searches
  again when you press **Submit**. Move the tea maker within Bluetooth range
  of Home Assistant or of a Bluetooth proxy.
- **The BRU app cannot connect.** The tea maker accepts one connection at a
  time, and Home Assistant stays connected. To use the app, disable the
  integration for a while (**Settings → Devices & services → BRU Tea Maker →
  ⋮ → Disable**). In the same way, while the app is connected, Home Assistant
  cannot reach the tea maker: close the app or turn off the phone's
  Bluetooth.
- **Entities go unavailable.** Home Assistant shows the tea maker as
  unavailable when the connection drops, tries to reconnect right away, and
  keeps retrying every 5 seconds. When the tea maker loses power, it takes
  about 10 seconds to notice.
- **"No free connections" through a proxy.** Each proxy has a limited number
  of connections, and Home Assistant keeps one open to the tea maker. Raise
  `max_connections` in the proxy's `esp32_ble` configuration (see
  [below](#esphome-bluetooth-proxy)), or disconnect another device from that
  proxy.
- **Home Assistant asks you to pair again.** The pairing was lost. Follow the
  prompt and pair as you did during setup. Home Assistant asks only after
  three failed reconnects in a row, so a single slow reconnect does not.
- **Pairing from scratch.** Remove the integration entry. On a local
  adapter, also remove the old pairing from the Home Assistant machine with
  `bluetoothctl remove AA:BB:CC:DD:EE:FF`. Then set it up again.
- **Reporting a problem.** On the device page, choose **Download
  diagnostics** and attach the file to the issue. It contains the recent
  messages to and from the tea maker and its firmware version, but not its
  Bluetooth address. For more detail, turn on debug logging, either with
  **Enable debug logging** on the integration's page or in
  `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.bru_tea_maker: debug
  ```

## ESPHome Bluetooth proxy

A standard ESPHome Bluetooth proxy cannot pair with the tea maker, because it
has no way to confirm the pairing code. Add the following to the proxy's
configuration, with your tea maker's Bluetooth address, and install it on the
proxy before setting up the integration:

```yaml
esp32_ble:
  io_capability: keyboard_display
  auth_req_mode: sc_mitm_bond
  max_connections: 4

bluetooth_proxy:
  active: true

ble_client:
  - mac_address: AA:BB:CC:DD:EE:FF  # your tea maker
    id: bru
    auto_connect: false
    on_numeric_comparison_request:
      then:
        - ble_client.numeric_comparison_reply:
            id: bru
            accept: true
```

The proxy then confirms the code on its own. During setup Home Assistant does
not show the code; you only accept it on the tea maker's screen. When the tea
maker is reached through a proxy, the setup dialog shows this configuration
with the tea maker's address already filled in.

To find the address otherwise: the **Add integration** device list shows it,
as `BRU… (AA:BB:CC:DD:EE:FF)`.

Tested with ESPHome 2026.9.

## Protocol notes

How the integration talks to the tea maker, including differences between
firmware versions, is described in [protocol.md](protocol.md).

## Disclaimer

This is an unofficial integration and is not affiliated with BRU. It was built
by studying how the tea maker talks over Bluetooth. Use it at your own risk.

## License

[MIT](LICENSE)
