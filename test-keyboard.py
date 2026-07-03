# test.py — cycle the keyboard through your four states
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor
import sys, time

STATES = {
    "working":  RGBColor(0, 0, 255),
    "needsyou": RGBColor(255, 160, 0),
    "done":     RGBColor(0, 255, 0),
    "failed":   RGBColor(255, 0, 0),
}

client = OpenRGBClient()           # needs OpenRGB running with SDK server on
kb = client.devices[0]             # or find the keyboard by name
kb.set_color(STATES[sys.argv[1]])