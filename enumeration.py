#!/usr/bin/env python3
"""List HID interfaces, highlighting Keychron devices and any QMK Raw HID endpoint."""
import hid

KEYCHRON_VID = 0x3434
QMK_RAW_USAGE_PAGE = 0xFF60
QMK_RAW_USAGE = 0x61

def main():
    devices = hid.enumerate()
    keychron = [d for d in devices if d["vendor_id"] == KEYCHRON_VID]

    if not keychron:
        print("No Keychron (VID 0x3434) devices found.")
        print("If the board is on Bluetooth/2.4GHz, switch it to WIRED and rerun.")
        print("\nAll HID vendor IDs currently seen:")
        for vid in sorted({d['vendor_id'] for d in devices}):
            print(f"  0x{vid:04X}")
        return

    print(f"Found {len(keychron)} Keychron HID interface(s):\n")
    raw_hid = None
    for d in keychron:
        up, us = d["usage_page"], d["usage"]
        is_raw = (up == QMK_RAW_USAGE_PAGE and us == QMK_RAW_USAGE)
        tag = "  <-- QMK Raw HID interface" if is_raw else ""
        print(f"  PID=0x{d['product_id']:04X}  "
              f"usage_page=0x{up:04X}  usage=0x{us:02X}  "
              f"product={d.get('product_string')!r}{tag}")
        if is_raw:
            raw_hid = d

    print()
    if raw_hid:
        print("Raw HID endpoint IS present. You can talk to it via hidapi.")
        print(f"  VENDOR_ID  = 0x{raw_hid['vendor_id']:04X}")
        print(f"  PRODUCT_ID = 0x{raw_hid['product_id']:04X}")
    else:
        print("No QMK Raw HID (0xFF60/0x61) interface exposed by the stock firmware.")
        print("Stock firmware likely doesn't route Raw HID; you'd need custom firmware")
        print("with RAW_ENABLE=yes, or use the VIA protocol / WLED strip instead.")

if __name__ == "__main__":
    main()