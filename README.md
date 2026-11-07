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
| Water temperature | Number | Water temperature for the next brew: 40–100 °C, in steps of 5. The lowest setting, 40 °C, is what the tea maker shows as **Cold**: the water is not heated. |
| Water quantity | Number | Amount of water for the next brew: 50–500 ml, in steps of 50. |
| Steeping time | Number | Steeping time for the next brew: 10–600 seconds, in steps of 10. |
| Preset | Select | Copies one of the tea maker's own presets into the three settings above. Shows **Custom** once a setting is changed by hand. |
| Brew | Button | Starts a brew with the current settings. |
| Sync presets | Button | Reads the presets from the tea maker again, after you change them on the machine. |

The brew settings live in Home Assistant, so they can be changed even while
the tea maker is out of range. A value between steps, such as 92 °C, is
rounded to the nearest one the tea maker accepts.

## Troubleshooting

- **Pairing failed.** The setup dialog says what went wrong and lets you try
  again. After a failed attempt the tea maker may refuse new ones for a few
  minutes; the dialog then suggests how long to wait.
- **The tea maker is not found.** Move it within Bluetooth range of Home
  Assistant.
- **Home Assistant asks you to pair again.** The pairing was lost. Follow the
  prompt and pair as you did during setup.

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
