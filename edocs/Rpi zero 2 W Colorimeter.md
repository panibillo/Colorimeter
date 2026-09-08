# Rpi zero 2 W Colorimeter Electrical parameters

## Rpi power lines


## Rpi dedicated GPIO pins




## DS18B20 bus line

- parasitic is compatible with non-parasitic: They can be run together
- Parasitic A pull-up resistor an caps help


# MOSFETS
    
## Use a logic level N-channel between Load and GND.
    | Part    | Vgs(th) | Id continuous | package | remark |
    | AO3400A | ~ 1V | 5.7A | SOT-23 | excellent |
    | IRLZ44N | 1-2V |  47 A | TO-220 | overkill |
    | 2N7000  | .8-3V | 200 mA | Through hole | too small |
    | BSS138  | 0.8-1.5V | 200 mA | SOT-23 | too small |

## Wiring 

    - V+ -- Load -- Gate 
    - GPIO -- R330 -- gate; gate -- R10K -- GND
    - Source -- GND
    
    R330 is a current limiting resistor, and GPIO protective.
    R10K pulls the gate current down to GND when off.

# Capacitors
## symbols:  
    `-||-`  non-polarized. Have no polarity marking.  
    - Multilayer ceramics MLCC, Class1(C0G/NP0), Class2(XR7, Y5V) and most other ceramics are unpolarized
     
    `+-|(--`  polarized.  Curved line is Cathode, connect to negative. 
    Dot, stripe, + or ->  marks the Anode, positive side
    - Tantalum and Electrolytic are polarized

### Ceramic caps

- 100nF (0.1µF) ceramic MLCC
- 16V or 25V rated
- 0603 or 0805 footprint (if SMD)
- X7R or C0G dielectric (both fine)

# Shift registers

A 74HC595 is the standard choice — 8-bit serial-in, parallel-out. You send 3 wires from the RPi (SPI or bit-banged), and get 8 independent outputs.


convert the comparison to a block of latex code that I can download.  start at \section level

Rewrite the latex "Pin Mapping Reference"
Advise reader to refer to https://pinout.xyz/ for detailed Rpi pinout information
Use minimal color in the document.  
Use academic style tables.

Review the Rpi pin mappings we determined (e.g. in LaTeX doc "Pin Mapping Reference".  I am making some modifications and want you to validate that they are valid and feasible.  
I should list all pin connections below (except ground)
Pins connected to the Adafruit 1651 are labeled as connecting to: TSC2007, IL19341, 1651, or 1651 LITE.
I have added a 74HC595 shift register to the circuit.
Verify that SPI1 lines correctly connect to the 74HC595 shift register
(The 74HC595 at present is the only device on SPI1)
Verify that GPIO labeled as "free" is available for general digital IO.
Any other sanity checks you can think of

Confirmations:
1 - fixed typo.
2 - Pin 36 GPIO16_SPI1-CE2 changed assigment to one-wire data
    Pin 26 GPIO7_SPI1-CE1 changed assigment to MOSFET-9
3 - fixed typo
4 - fixed J1 pin assinments
5 - fixed GPIO numbers
6 - I am installing an optional SPI1 header and routing both MISO and MOSI to it.
    Question: If 

swap pin 11 and pin 29: 
29  GPIO17    to SD CS on 1651  via J1 pin 8
11  GPIO5   btn or led at J1 pin 14

Pin 12 is still a question mark for me. do I need to connect anything to 
New assignments.
RPI zero pin mappings:
pin Name    Use
1   3.3V    NO CONNECT
3   I2C:SDA  to TSC2007  via J1 pin 2
5   I2C:SCL  to TSC2007  via J1 pin 4
7   GPIO4    TSIRQ to TSC2007  via J1 pin 10
9   GND      to 1651  via J1 pin 6
11  GPIO5   btn or led at J1 pin 14
13  NO CONNECT
15  NO CONNECT
17  3.3V       to J5 (optional SPI1 header) pin 1 
19  SPI0:MOSI  to ILI9341  via J1 pin 12
21  SPI0:MISO  to ILI9341  via J1 pin 13
23  SPI0:SCLK  to ILI9341  via J1 pin 11
25  GND   to interface via J1 pin 15
27  NO CONNECT
29  GPIO17    to SD CS on 1651  via J1 pin 8
31  GPIO6   to TFT DC pin 9 via J1 pin 7
33  GPIO13:PWM  to 1651 LITE  via J1 pin 9
35  SPI1:MISO   to J5 (optional SPI1 header) pin 4 
37  GPIO26  btn or led at J1 pin 16
2   +5V
4   +5V     to 1651  via J1 pin 1
6   GND     to 1651  via J1 pin 3  
8   NO CONNECT 
10  NO CONNECT
12  SPI1 CE0 reserved - Connected to 74HC595 Pin 12?
16  GPIO23 free
18  GPIO24 free
22  GPIO25  MOSFET-10
24  SPI0:CS to IL19341
26  GPIO7-CE1   MOSFET-9
28  NO CONNECT
32  GPIO12:PWM  Controls Buzzer 
36  GPIO16-CE2  One-wire data
38  SPI1:MOSI  to 74HC595 shift register pin 14; to J5 (optional SPI1 header) pin 5
40  SPI1:CLK   to 74HC595 shift register pin 11; to J5 (optional SPI1 header) pin 3

Make your review very carefully.  Tell me the references that you are using, and give me the urls if available.

give me a latex section (beginning at \section{Magnetic buzzer})  describing the buzzer and the control circuit, parts list, programming notes, and example python method code sound_alert(frequency, volume, duration). At this point, don't worry about logic to interrupt before duration is over.  
