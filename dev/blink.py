""" Blink colorboard_1 from mainboard_C

Setup the rpi zero:
    Edit boot/firmware/config.txt
        # Initialize GPIO pins 
        #   pins and values match hardware
        #   op = output
        #   dl = drive low
        # Values for ColorCam mainboard_C driving test_light_1 light board
        #   Set LED pins to LOW at boot time to keep LED's off. 
        #   (they might flicker a moment at boot time)
        gpio=5=op,dl   
        gpio=6=op,dl
        gpio=22=op,dl
        gpio=26=op,dl
        gpio=27=op,dl


"""


from gpiozero import LED
from time import sleep

RED = 5
GREEN = 6
WHITE = 22
BLUE = 26
VIOLET = 27

Wled = LED(WHITE) # Initialize an LED object on GPIO pin 17
Rled = LED(RED)
Gled = LED(GREEN)
Bled = LED(BLUE)
Vled = LED(VIOLET)

while True:
    for led, msg in ((Wled,'W'), (Rled, 'R'),(Gled,'G'),(Bled,'B'),(Vled,'V')):
        print(f"Blink {msg}")
        led.on()      # Turn on the LED (set pin to HIGH)
        sleep(0.2)      # Wait for 1 second
        led.off()     # Turn off the LED (set pin to LOW)
        sleep(1)      # Wait for another second


