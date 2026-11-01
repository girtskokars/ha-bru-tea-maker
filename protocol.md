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
- The integration's original code, written against firmware that differs from
  `061026-1` in two ways (see [Firmware differences](#firmware-differences)).

The app reads an 8-character ASCII value (`061026-1`) from a GATT
characteristic on every connection. It is treated here as the firmware version.
The characteristic's UUID was not confirmed.

A capture file is a standard btsnoop v1 file (datalink 1002, H4 framing). BRU
traffic is ATT on L2CAP channel 4, and every BRU frame starts with `0xff`.

## Transport

- Commands are written to `aae28f02-71b5-42a1-8c3c-f9cf6ac969d0`. Replies
  arrive as notifications on `aae28f01-71b5-42a1-8c3c-f9cf6ac969d0`, which must
  be subscribed to. The UUIDs come from the integration's `const.py`, and the
  integration uses them successfully on firmware `061026-1` (verified). The
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
from the integration's original code, and the 60-byte preset reply was seen in
integration logs.

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
| Temperature | `40 + 5 × code` °C (codes 0, 8 and 12 seen: 40, 80 and 100 °C) |
| Brew time | `10 × (code + 1)` s |
| Water | `50 × (code + 1)` ml |

The measured length of the "steeping" status phase does not equal the brew time
(29 s for a 30 s brew, 120 s for a 180 s brew, where the machine counted down
from 3:00). How the phases relate to the brew time is unknown.

## Presets

The reply to `0x50` has this payload: the slot (1 byte), the brew time code, the
temperature code, the water code, and then the 48-byte name (UTF-16LE,
zero-padded). The parameter order differs from the start command, which sends
temperature, then brew time, then water. The machine's history records use the
same order as presets, followed by a byte that was `09` in every record seen
(meaning unknown). On firmware `061026-1` four `ff` bytes follow the name field,
so the frame is 60 bytes instead of 56.

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
unknown.

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
- **Reply.** `00` means accepted. The brew shows as `brewing` on the next status
  poll. `01` means refused and nothing starts. (Verified for both values;
  treating `01` as "refused" is inferred from the machine not brewing.)

## Firmware differences

| | Original code assumes | Firmware `061026-1` |
|---|---|---|
| Preset reply | 56-byte frame | 60-byte frame: the 48-byte name field is followed by `ff ff ff ff` |
| Start brew | short layout | short layout refused (`01`, three of three attempts); extended layout accepted |

The integration accepts both preset lengths and tries the extended start frame
first, then the short one if BRU refuses it or does not answer. Behaviour on
older firmware is assumed from the original code and has not been tested.

## Status

The status reply payload is a status code, a sub-code, and four bytes that were
`12 34 56 78` in every reply seen (meaning unknown). The names in `const.py` are
the integration's own labels. Observed on `061026-1`:

| Code / sub-code | Seen as |
|---|---|
| 1 / 2 | `standby` |
| 2 / 4 | `function_menu`: the machine is awake showing its menu |
| 5 / 4, then 5 / 25, 26, 28, 29, 30 | `brewing`, then `filling`, `steeping`, `serving`, `rinsing`, `enjoy` |
| 12 / 123 | `warning_message` with the `open_lid` prompt after a brew |

Sub-codes 120 to 133 are on-screen prompts, which the message sensor shows. The
machine returned to standby by itself after waking (87 s and 105 s observed).

## Connection sequences

The integration: connect, pair (resume the bond), subscribe, read the presets,
then poll status (every 5 s in standby, every 1 s while awake).

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
- **Other values.** The meaning of reply payloads other than `00` and `01`, of
  the constant bytes noted above, and of the other bytes in the setup reply.
