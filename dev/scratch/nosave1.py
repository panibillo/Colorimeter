# ── TSC2007 measurement commands ──────────────────────────────────────────────
# Constants copied from adafruit_tsc2007 copyright ladyada, SPDX-License-Identifier: MIT  
TSC2007_MEASURE_TEMP0 = 0
TSC2007_MEASURE_AUX = 2
TSC2007_MEASURE_TEMP1 = 4
TSC2007_ACTIVATE_X = 8
TSC2007_ACTIVATE_Y = 9
TSC2007_ACTIVATE_YPLUS_X = 10
TSC2007_SETUP_COMMAND = 11
TSC2007_MEASURE_X = 12
TSC2007_MEASURE_Y = 13
TSC2007_MEASURE_Z1 = 14
TSC2007_MEASURE_Z2 = 15

TSC2007_POWERDOWN_IRQON = 0
TSC2007_ADON_IRQOFF = 1
TSC2007_ADOFF_IRQON = 2

TSC2007_ADC_12BIT = 0
TSC2007_ADC_8BIT = 1

def fmt(v):
    return f"={v:08b}  0x{v:2X} {v:03d}"
# PD=11 (0x0C) keeps the device on and disables PENIRQ during the read
# sequence; _CMD_END (PD=00) re-enables PENIRQ after all three channels.
_CMD_X   = 0xCC   # X position,  PD=11 (device on, PENIRQ off)
_CMD_Y   = 0xDC   # Y position,  PD=11
_CMD_Z1  = 0xEC   # Z1 pressure, PD=11
_CMD_END = 0x00   # TEMP0, PD=00 — power-down and re-enable PENIRQ

print (f"hardcode   X {fmt(_CMD_X)},  Y {fmt(_CMD_Y)},  Z1 {fmt(_CMD_Z1)},  END { fmt(_CMD_END)}")

_CMD_X = ((TSC2007_MEASURE_X & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
_CMD_Y = ((TSC2007_MEASURE_Y & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
_CMD_Z1 = ((TSC2007_MEASURE_Z1 & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
_CMD_END = ((TSC2007_MEASURE_TEMP0 & 0x0F) << 4) | ((TSC2007_POWERDOWN_IRQON & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)

print (f"Adafruit   X {fmt(_CMD_X)},  Y {fmt(_CMD_Y)},  Z1 {fmt(_CMD_Z1)},  END { fmt(_CMD_END)}")

# print ("B",fmt(_CMD_X), fmt(_CMD_Y), fmt(_CMD_Z1), fmt(_CMD_END))

_CMD_X = ((TSC2007_MEASURE_X & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_12BIT & 0x01) << 1)
print (f"12 bit   X {fmt(_CMD_X)}")
_CMD_X = ((TSC2007_MEASURE_X & 0x0F) << 4) | ((TSC2007_ADON_IRQOFF & 0x03) << 2) | ((TSC2007_ADC_8BIT & 0x01) << 1)
print (f" 8 bit   X {fmt(_CMD_X)}")

print (f"(TSC2007_MEASURE_X & 0x0F) << 4    {TSC2007_MEASURE_X  :04b}  {fmt((TSC2007_MEASURE_X & 0x0F) << 4)}  ")
print (f"(TSC2007_ADOFF_IRQON & 0x03) << 2  {TSC2007_ADOFF_IRQON:04b}  {fmt((TSC2007_ADOFF_IRQON & 0x03) << 2)}")
print (f"(TSC2007_ADON_IRQOFF & 0x03) << 2  {TSC2007_ADON_IRQOFF:04b}  {fmt((TSC2007_ADON_IRQOFF & 0x03) << 2)}")
print (f"(TSC2007_ADC_12BIT & 0x01) << 1    {TSC2007_ADC_12BIT  :04b}  {fmt((TSC2007_ADC_12BIT & 0x01) << 1)}")

samp   (172, 134, 34, 34)
ul     ( 60,  60, 60, 60)  upper left
ll     ( 60, 180, 60, 60)  lower left
ur     (260,  60, 60, 60)  upper right
lr     (260, 180, 60, 60)  lower right

print ('=========== DONE ===========')