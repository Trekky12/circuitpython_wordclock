"""CircuitPython Essentials Storage logging boot.py file"""
import board
import digitalio
import storage

switch = digitalio.DigitalInOut(board.IO12)
switch.direction = digitalio.Direction.INPUT
switch.pull = digitalio.Pull.UP

# Mount the storage readonly when the button is pressed
readonly = (not switch.value)

storage.remount("/", readonly)
