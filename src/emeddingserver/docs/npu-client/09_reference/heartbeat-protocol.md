# Heartbeat Protocol (Binary)

## Ziel

- minimale Bandbreite
- schnelle Auswertung

## Paketformat (12 Byte)

```text
Magic      2 Byte
Version    1 Byte
Type       1 Byte
WorkerID   2 Byte
SlotID     1 Byte
Flags      1 Byte
Load       1 Byte
Queue      1 Byte
Seq        1 Byte
CRC8       1 Byte
```

## Hinweise

- Paket wird als fester 12-Byte Frame serialisiert.
- CRC8 wird ueber die ersten 11 Bytes gebildet.
- Bei ungueltigem Magic/Version/Type/CRC muss das Paket verworfen werden.
