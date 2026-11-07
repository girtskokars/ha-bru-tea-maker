# BRU Maker One Bluetooth protocol notes

These notes describe how the integration talks to the tea maker. They are
reverse-engineered, not vendor documentation, so each statement says how well
it is supported:

- **Verified**: seen directly in a capture or log from real hardware.
- **Inferred**: consistent with what was seen, but not tested.
- **Unknown**: not understood.

## Sources

- Android HCI snoop captures of the official BRU app starting two brews
  (Android 13): a "Freestyle" brew at 40 °C, 50 ml, 30 s, and a saved recipe at
  100 °C, 350 ml, 180 s.
- Integration debug logs and Home Assistant history from a unit reporting
  `061026-1`, connected through an ESPHome Bluetooth proxy (October 2026).
- Integration debug logs from a second unit (`BRU.M2-1422E73F`) on a local USB
  adapter: on firmware `160626-2` (6 November 2026), then on `061026-1` after a
  firmware update (7 November 2026).
- The integration's original code, written against firmware `160626-2` or
  similar (see [Firmware differences](#firmware-differences)).

A capture file is a standard btsnoop v1 file (datalink 1002, H4 framing). BRU
traffic is ATT on L2CAP channel 4, and every BRU frame starts with `0xff`.

## Device information

BRU has the standard Device Information service (`0x180A`). Values read on
firmware `061026-1` (verified):

| Characteristic | Value |
|---|---|
| Firmware Revision String (`0x2A26`) | `061026-1` |
| Software Revision String (`0x2A28`) | `061026-1` |
| Hardware Revision String (`0x2A27`) | `V1.0.1` |
| Model Number String (`0x2A24`) | `BRU-1` |
| Manufacturer Name String (`0x2A29`) | `BRU AG` |
| System ID (`0x2A23`) | not read |

There is no Serial Number String (`0x2A25`). The app reads an 8-character
ASCII value (`061026-1`) on every connection, so it most likely reads `0x2A26`
or `0x2A28` (inferred).

## Advertising

- The tea maker advertises as `BRU.M2-` followed by the last four bytes of its
  address: `BRU.M2-1422E73F` for `F4:14:14:22:E7:3F` (verified on one unit).
- During a firmware update it advertises as `BRU_U`, at its address plus one
  (`F4:14:14:22:E7:40`), and is back under its usual name and address
  afterwards (verified).
- Whether it advertises while connected is unknown.

## Transport

- Commands are written to `aae28f02-71b5-42a1-8c3c-f9cf6ac969d0`. Replies
  arrive as notifications on `aae28f01-71b5-42a1-8c3c-f9cf6ac969d0`, which must
  be subscribed to. The UUIDs come from the integration's `const.py`, and the
  integration uses them successfully on both firmware versions (verified). The
  captures contain no GATT discovery, so they show attribute handles but not
  which UUID each handle belongs to. The app subscribed to notifications on
  every BRU connection in the captures (verified).
- The link must be encrypted before BRU accepts anything, including the
  notification subscription. Resuming an existing bond takes well under a
  second. The first pairing needs the machine in pairing mode. (Original code
  comments; resume time verified from proxy logs.)

## Frame format

```
FF | length | opcode | payload ... | checksum
```

- `length` is the size of the whole frame, including `FF` and the checksum.
- `checksum = (-(sum of all preceding bytes) XOR 0x3A) & 0xFF`.
- A reply carries the command's opcode plus one.

Verified: both captured start frames pass this checksum, and the integration
rejects any notification that does not.

## Commands

| Opcode | Meaning | Command payload | Reply | Seen in captures | Used by integration |
|---|---|---|---|---|---|
| `0x08` | Set clock | 4 bytes, little-endian seconds | none seen | every connection | no |
| `0x2A` | Read setup | none | `0x2B`, 8-byte frame, payload byte 3 is the preset count | no | yes |
| `0x50` | Read preset | 1 byte, slot from 1 | `0x51`, see [Presets](#presets) | no | yes |
| `0x44` | Read status | none | `0x45`, 10-byte frame, see [Status](#status) | polled every 0.6 to 0.7 s | yes |
| `0x40` | Start brew | see [Start brew](#start-brew-0x40) | `0x41`, 5-byte frame, payload `00` accepted or `01` refused | yes | yes |
| `0x54` | Read brew-history record | none | `0x55`, 138-byte frame | yes | no |
| `0x57` | Acknowledge history record | 4 bytes: record index, then `c1 7f ff` | `0x58`, payload `00` | yes | no |

`0x2A` and `0x50` replies were not in the captured windows; their layout comes
from the integration's original code, and both preset reply lengths were seen
in integration logs. The setup reply was `ff082b00010003f0` (three presets) on
both firmware versions.

The clock payload is the phone's local wall-clock time encoded as if it were UTC.
In capture 1 it equals the capture's own timestamp (which is also the phone's
local time) to the second. In capture 2 the payload was exactly 24 hours earlier
than the phone's own clock at both clock syncs, with the time of day correct.
That was not investigated.

## Parameter encoding

All three brew parameters are one byte each (verified against the values the
app displayed and against the machine's own history records):

| Parameter | Value |
|---|---|
| Temperature | `40 + 5 × code` °C (codes 0, 8, 11 and 12 seen: 40, 80, 95 and 100 °C) |
| Brew time | `10 × (code + 1)` s |
| Water | `50 × (code + 1)` ml |

## Presets

The reply to `0x50` has this payload: the slot (1 byte), the brew time code, the
temperature code, the water code, and then the 48-byte name (UTF-16LE,
zero-padded). The parameter order differs from the start command, which sends
temperature, then brew time, then water. The machine's history records use the
same order as presets, followed by a byte that was `09` in every record seen
(meaning unknown). The frame length depends on the firmware (see
[Firmware differences](#firmware-differences)).

## Start brew (0x40)

Payload layouts, by offset:

| Offset | Size | Short layout | Extended layout |
|---|---|---|---|
| 0 | 1 | `00` | `00` |
| 1 | 1 | temperature code | temperature code |
| 2 | 1 | brew time code | brew time code |
| 3 | 1 | water code | water code |
| 4 to 6 | 3 | `00 00 00` | `00 00 00` |
| 7 | 1 | `01` | `01` |
| 8 to 10 | 3 | `00 00 00` | `00 00 00` |
| 11 to 18 | 8 | not sent | initiator |
| 19 to 66 | 48 | not sent | program name, UTF-16LE, zero-padded |
| 67 to 70 | 4 | not sent | recipe |

The short layout makes a 15-byte frame, and the extended layout a 75-byte
frame. Bytes 4 to 10 were identical in every frame seen; their meaning is
unknown. Which layout BRU accepts depends on the firmware (see
[Firmware differences](#firmware-differences)).

- **Initiator.** The app sent an 8-byte value that was identical in both
  captures, so it looks specific to the app installation and is not recorded
  here. The machine's own history records show eight `ff` bytes for a brew
  started on the machine. The integration sends eight `ff` bytes, and BRU
  accepted that. (Verified.)
- **Name.** The machine records it in its history. The integration sends
  `Home Assistant`, which was accepted. (Verified.)
- **Recipe.** Four `ff` bytes mean no recipe. The app sent a different 4-byte
  value for a saved recipe, and the same value appears in the machine's history
  record for that brew. That it is a recipe identifier is inferred.
- **Reply.** `00` means accepted. The reply comes within about 25 ms, and the
  brew shows as `brewing` on the next status poll. `01` means refused and
  nothing starts. (Verified for both values; treating `01` as "refused" is
  inferred from the machine not brewing.)
- **No cup or lid open.** BRU accepts the start (`00`) anyway, from standby and
  from the menu. About a second later it shows "Place cup" (`12/120`) or
  "Close lid" (`12/122`) instead of brewing. With the lid open, the brew starts
  by itself as soon as the lid is closed (`061026-1`, verified once). With no
  cup, "Place cup" stayed until it was cleared on the machine, which then went
  back to its menu without brewing (`160626-2`, verified once). So `00` does
  not mean the brew is running: only the status shows that.

## Firmware differences

| | `160626-2` | `061026-1` |
|---|---|---|
| Preset reply | 56-byte frame | 60-byte frame: the 48-byte name field is followed by `ff ff ff ff` |
| Short start frame | accepted (`00`) | refused (`01`, three of three attempts) |
| Extended start frame | refused (`01`, within 14 ms) | accepted (`00`) |
| Between the first brewing frame and filling | `5/227` | nothing |

All verified, the last row from three brews on `160626-2` and two on
`061026-1`. The `160626-2` column is from the second unit before its update; the
same unit on `061026-1` behaved like the first unit, with 60-byte presets and
the extended frame accepted on the first try. Taking the version strings as
dates (`160626` as 16 June 2026, `061026` as 6 October 2026), `160626-2` is the
older firmware (inferred).

The integration accepts both preset lengths and tries the extended start frame
first, then the short one if BRU refuses it or does not answer. On `160626-2`
the fallback adds about 40 ms.

## Status

The status reply payload is a status code, a sub-code, and four bytes that were
`12 34 56 78` in every reply seen (meaning unknown). The names in `const.py` are
the integration's own labels; the display texts were read off the machine on
`160626-2`. Observed on both firmware versions unless noted:

| Code / sub-code | Integration state | Display | Seen |
|---|---|---|---|
| 0 / 3 | `starting` | not read | about 2.5 s at power-on (`061026-1`); 1 s between standby and the menu (`160626-2`) |
| 1 / 2 | `standby` | | |
| 2 / 4, 2 / 10 | `function_menu` | the menu | both sub-codes after brews; what tells them apart is unknown |
| 5 / sub-code of the previous status | `brewing` | | first frame after a start: `5/2` from standby, `5/4` and `5/10` from the menu |
| 5 / 227 | `brewing` | | 1 to 2 s before filling, `160626-2` only |
| 5 / 25 | `filling` | Filling Up | |
| 5 / 26 | `steeping` | Steeping | |
| 5 / 28 | `serving` | Serving | |
| 5 / 29 | `rinsing` | Rinse to cup | with the machine's rinse setting at "cup" |
| 5 / 30 | `enjoy` | Enjoy bru | |
| 12 / 120 | `warning_message`, `place_cup` | Place cup | after a start without a cup |
| 12 / 122 | `warning_message`, `close_lid` | Close lid | after a start with the lid open |
| 12 / 123 | `warning_message`, `open_lid` | Open Lid | after a brew, from the machine's lid reminder setting, which also sets the delay (1 s here); shown until the lid is opened |

Sub-codes 120 to 133 are on-screen prompts, which the message sensor shows;
only the three above have been seen. The machine returned to standby by itself
after waking (87 s and 105 s observed).

### Phase timings

Status changes during brews started from Home Assistant or on the machine
(verified, at 1 s polling, so each value is ±1 s):

| Firmware | Water | Brew time | Filling | Steeping | Serving | Rinsing | Enjoy |
|---|---|---|---|---|---|---|---|
| `160626-2` | 250 ml | 300 s | 41 s | 291 s | 18 s | 6 s | 3 s |
| `160626-2` | 250 ml | 300 s | 41 s | 292 s | 18 s | 6 s | 3 s |
| `061026-1` | 100 ml | 250 s | 17 s | 243 s | 9 s | 6 s | 3 s |
| `061026-1` | 100 ml | 250 s | 17 s | 244 s | 8 s | 6 s | 3 s |

Filling and serving scale with the water amount. Steeping was 6 to 9 s shorter
than the brew time in these brews, and 1 s shorter for the 30 s app brew. In
the 180 s app brew it lasted 120 s although the machine counted down from 3:00;
that is not explained.

## Connection sequences

The integration: connect, pair (resume the bond), subscribe, read the device
information and the presets, then poll status (every 5 s in standby, every 1 s
while awake).

The app, as captured: read the firmware string, poll status, set the clock,
subscribe, request a history record, start the brew. After the brew it reads
and acknowledges each history record (`0x54`, then `0x57`), unsubscribes and
disconnects.

The integration sends neither the clock nor the history commands, and starting a
brew works without them. (Verified.)

## Not yet explained

- **Reconnect loop.** On one day the link was hung up by the machine (HCI
  reason `0x13`, remote user terminated) about 3 s after each connection, with a
  reconnect roughly every 6 s at a 5 s standby poll. It did not recur in about
  17 hours of standby testing at 2 s, 3 s and 5 s polling. Afterwards there were
  about 0.3 unprompted reconnects an hour at 2 s and at 5 s. The cause is
  unknown.
- **Wake on reconnect.** Twice a reconnect coincided with the machine waking into
  its menu. The cause is unknown.
- **History records.** The integration never acknowledges them. The effect, if
  any, is unknown. A record appears to hold the initiator, a timestamp, the
  name, the recipe and the three parameter codes.
- **Concurrent connections.** Whether BRU accepts a second central while Home
  Assistant is connected was not tested.
- **Status sub-codes.** What `5/227` and the menu's `2/4` versus `2/10` mean.
- **Other values.** The meaning of reply payloads other than `00` and `01`, of
  the constant bytes noted above, and of the other bytes in the setup reply.
