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

1. Put the tea maker in pairing mode from its Bluetooth menu.
2. In Home Assistant, go to **Settings → Devices & services**. The tea maker is
   usually discovered on its own; otherwise choose **Add integration** and
   search for **BRU Tea Maker**.
3. Home Assistant shows a 6-digit code, and the tea maker shows the same one.
   Accept the code on the tea maker's screen, then press **Submit** in Home
   Assistant.

Pairing is only needed once.

## Entities

| Entity | Type | What it does |
| --- | --- | --- |
| Status | Sensor | What the tea maker is doing: standby, filling, steeping, serving, and so on. |
| Message | Sensor | The prompt on the tea maker's screen, such as "Place cup" or "Add water". |
| Temperature | Number | Water temperature for the next brew: 40–100 °C, in steps of 5. |
| Water amount | Number | Amount of water for the next brew: 50–500 ml, in steps of 50. |
| Brew time | Number | Steeping time for the next brew: 10–600 seconds, in steps of 10. |
| Preset | Select | Copies one of the tea maker's own presets into the three settings above. |
| Start brew | Button | Starts a brew with the current settings. |
| Sync presets | Button | Reads the presets from the tea maker again, after you change them on the machine. |

## Troubleshooting

- **Pairing failed.** Check that the tea maker is in pairing mode, then wait
  about five minutes before trying again. The tea maker refuses new attempts
  for a while after a failed one.
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
not show the code; you only accept it on the tea maker's screen.

Tested with ESPHome 2026.9.

## Protocol notes

How the integration talks to the tea maker, including differences between
firmware versions, is described in [protocol.md](protocol.md).

## Disclaimer

This is an unofficial integration and is not affiliated with BRU. It was built
by studying how the tea maker talks over Bluetooth. Use it at your own risk.

## License

[MIT](LICENSE)
