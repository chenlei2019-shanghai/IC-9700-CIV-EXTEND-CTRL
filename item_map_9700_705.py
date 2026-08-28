"""IC-9700 -> IC-705 CI-V "1A 05" (SET item) number mapping.

Sources:
  - civ_ref_text.txt    : IC-9700 CI-V Reference Guide (command table, 1A 05 items 0001~0339)
  - ic705_civ_ref.txt   : IC-705 CI-V Reference Guide  (command table, 1A 05 items 0001~0385)

Matching was done by the English function description of each item.
Keys/values are ints equal to the 4-digit BCD item number printed in the
manuals (e.g. item "0106" -> 106). Only items present on BOTH radios are
included. IC-9700-only items (satellite, SUB band, 1200M, ACC, LAN/Network,
TX PWR LIMIT, per-band scope fixed edges, ...) and IC-705-only items (WFM tone,
Home CH beep, Max TX Power, WLAN, battery, GPS logger, tuner/AH-705,
front-key customize, HF-band fixed edges, ...) are NOT mapped.

Caveats (same item, different data encoding on the IC-705):
  - 0050->0053 SPEECH Language: value order is REVERSED
    (9700: 00=English,01=Japanese / 705: 00=Japanese,01=English).
  - 0072->0089 REF Adjust: IC-705 range is 0000~0511 (coarse+fine combined);
    IC-9700 0073 REF Adjust (FINE) has no IC-705 counterpart.
  - 0030->0033 Band Edge Beep: 9700 uses data 00/01/02/03, 705 uses 00~03 (same set).
  - 0046->0049 Auto Repeater: 705 adds 02=ON(DUP,TONE).
  - 0075->0091 Auto Reply: 705 adds 03=Position.
  - 0115/0116->0118/0119 DATA OFF MOD/DATA MOD: value lists differ
    (9700: MIC/ACC/USB/LAN based / 705: MIC/USB/WLAN based).
  - 0134->0134 GPS Out: 9700 01=DATA->USB(B), 705 01=ON.
  - 0162->0147 RX Position Display: 9700 has 02=ON(Main Only), 705 is 00/01 only.
  - 0191->0177 VBW: 9700 uses a table value, 705 uses 00=Narrow/01=Wide.
  - 0228->0256 MIC Up/Down Keyer: 705 adds 02=ON(A/B).
  - 0255->0281 GPS Select: 9700 01=External GPS, 705 01=ON (internal).
  - 0319->0354 GPS Auto TX: timer value lists differ.
  - NB items 0321~0326 are per-band (144M/430M) on the IC-9700; the IC-705 has
    a single set (0356~0358) for the selected band. 1200M NB (0327~0329) unmapped.
"""

# IC-9700 item -> IC-705 item  (int value of the 4-digit BCD item number)
IC9700_TO_IC705 = {
    1: 1,  # 0001 -> 0001  Tone Control/TBW RX SSB RX HPF/LPF settings
    2: 2,  # 0002 -> 0002  Tone Control/TBW RX SSB RX Tone level
    3: 3,  # 0003 -> 0003  Tone Control/TBW RX SSB RX Tone level
    4: 4,  # 0004 -> 0004  Tone Control/TBW RX AM RX HPF/LPF settings
    5: 5,  # 0005 -> 0005  Tone Control/TBW RX AM RX Tone level
    6: 6,  # 0006 -> 0006  Tone Control/TBW RX AM RX Tone level
    7: 7,  # 0007 -> 0007  Tone Control/TBW RX FM RX HPF/LPF settings
    8: 8,  # 0008 -> 0008  Tone Control/TBW RX FM RX Tone level
    9: 9,  # 0009 -> 0009  Tone Control/TBW RX FM RX Tone level
    10: 10,  # 0010 -> 0010  Tone Control/TBW RX DV RX HPF/LPF settings
    11: 11,  # 0011 -> 0011  Tone Control/TBW RX DV RX Tone level
    12: 12,  # 0012 -> 0012  Tone Control/TBW RX DV RX Tone (Treble) level
    13: 15,  # 0013 -> 0015  Tone Control/TBW RX CW RX HPF/LPF settings
    14: 16,  # 0014 -> 0016  Tone Control/TBW RX RTTY RX HPF/LPF settings
    15: 17,  # 0015 -> 0017  Tone Control/TBW TX SSB TX Tone level
    16: 18,  # 0016 -> 0018  Tone Control/TBW TX SSB TX Tone level
    17: 19,  # 0017 -> 0019  Tone Control/TBW TX SSB TX bandwidth for wide
    18: 20,  # 0018 -> 0020  Tone Control/TBW TX SSB TX bandwidth for mid
    19: 21,  # 0019 -> 0021  Tone Control/TBW TX SSB TX bandwidth for narrow
    20: 22,  # 0020 -> 0022  Tone Control/TBW TX SSB-D TX passband width
    21: 23,  # 0021 -> 0023  Tone Control/TBW TX AM TX Tone level
    22: 24,  # 0022 -> 0024  Tone Control/TBW TX AM TX Tone level
    23: 25,  # 0023 -> 0025  Tone Control/TBW TX FM TX Tone level
    24: 26,  # 0024 -> 0026  Tone Control/TBW TX FM TX Tone level
    25: 27,  # 0025 -> 0027  Tone Control/TBW TX DV TX Tone level
    26: 28,  # 0026 -> 0028  Tone Control/TBW TX DV TX Tone level Cmd. Sub cm
    27: 29,  # 0027 -> 0029  Function Beep Level
    28: 30,  # 0028 -> 0030  Function Beep Level Limit
    29: 31,  # 0029 -> 0031  Function Beep
    30: 33,  # 0030 -> 0033  Function Band Edge Beep Function Band Edge Beep)
    38: 41,  # 0038 -> 0041  Function TX Delay 144M
    39: 42,  # 0039 -> 0042  Function TX Delay 430M
    41: 43,  # 0041 -> 0043  Function Time-Out Timer
    42: 44,  # 0042 -> 0044  Function PTT Lock
    43: 45,  # 0043 -> 0045  Function SPLIT Quick SPLIT
    44: 46,  # 0044 -> 0046  Function SPLIT FM SPLIT Offset
    45: 47,  # 0045 -> 0047  Function SPLIT SPLIT LOCK
    46: 49,  # 0046 -> 0049  Function Auto Repeater for USA version)
    47: 50,  # 0047 -> 0050  Function RTTY Mark Frequency
    48: 51,  # 0048 -> 0051  Function RTTY Shift Width
    49: 52,  # 0049 -> 0052  Function RTTY Keying Polarity
    50: 53,  # 0050 -> 0053  Function SPEECH SPEECH Language
    51: 54,  # 0051 -> 0054  Function SPEECH Alphabet
    52: 55,  # 0052 -> 0055  Function SPEECH SPEECH Speed
    53: 56,  # 0053 -> 0056  Function SPEECH RX Call Sign SPEECH, 02=ON)
    54: 57,  # 0054 -> 0057  Function SPEECH RX>CS SPEECH
    55: 59,  # 0055 -> 0059  Function SPEECH S-Level SPEECH Cmd. Sub cmd.
    56: 60,  # 0056 -> 0060  Function SPEECH MODE SPEECH
    57: 61,  # 0057 -> 0061  Function SPEECH SPEECH Level
    58: 62,  # 0058 -> 0062  Function [SPEECH/LOCK] Switch
    59: 63,  # 0059 -> 0063  Function Lock Function
    60: 64,  # 0060 -> 0064  Function Memo Pad Quantity
    61: 65,  # 0061 -> 0065  Function MAIN DIAL Auto TS
    62: 66,  # 0062 -> 0066  Function MIC Up/Down Speed
    64: 67,  # 0064 -> 0067  Function [NOTCH] Switch
    65: 68,  # 0065 -> 0068  Function [NOTCH] Switch
    66: 69,  # 0066 -> 0069  Function SSB/CW Synchronous Tuning
    67: 70,  # 0067 -> 0070  Function CW Normal Side
    68: 85,  # 0068 -> 0085  Function Screen Keyboard Type
    69: 86,  # 0069 -> 0086  Function Screen Full Keyboard Layout
    70: 87,  # 0070 -> 0087  Function Screen Capture [POWER] Switch
    71: 88,  # 0071 -> 0088  Function Screen Capture File Type
    72: 89,  # 0072 -> 0089  Function REF Adjust
    74: 90,  # 0074 -> 0090  DV/DD Set Standby Beep, 03=ON)
    75: 91,  # 0075 -> 0091  DV/DD Set Auto Reply
    76: 92,  # 0076 -> 0092  DV/DD Set DV Data TX
    77: 93,  # 0077 -> 0093  DV/DD Set DV Fast Data Fast Data
    78: 94,  # 0078 -> 0094  DV/DD Set DV Fast Data GPS Data Speed
    79: 95,  # 0079 -> 0095  DV/DD Set DV Fast Data TX Delay
    80: 96,  # 0080 -> 0096  DV/DD Set Digital Monitor
    81: 97,  # 0081 -> 0097  DV/DD Set Digital Repeater Set
    82: 98,  # 0082 -> 0098  DV/DD Set DV Auto Detect
    83: 99,  # 0083 -> 0099  DV/DD Set RX Record
    84: 100,  # 0084 -> 0100  DV/DD Set BK
    85: 101,  # 0085 -> 0101  DV/DD Set EMR
    86: 102,  # 0086 -> 0102  DV/DD Set EMR AF Level
    89: 103,  # 0089 -> 0103  QSO/RX Log QSO Log Cmd. Sub cmd.
    90: 104,  # 0090 -> 0104  QSO/RX Log RX History Log
    91: 105,  # 0091 -> 0105  QSO/RX Log CSV Format Separator/Decimal
    92: 106,  # 0092 -> 0106  QSO/RX Log CSV Format Date
    97: 108,  # 0097 -> 0108  Connectors Phones Level
    105: 109,  # 0105 -> 0109  Connectors USB AF/IF Output Output Select
    106: 110,  # 0106 -> 0110  Connectors USB AF/IF Output AF Output Level
    107: 111,  # 0107 -> 0111  Connectors USB AF/IF Output AF SQL, 01=ON)
    108: 112,  # 0108 -> 0112  Connectors USB AF/IF Output AF Beep/Speech... Ou
    109: 113,  # 0109 -> 0113  Connectors USB AF/IF Output IF Output Level
    113: 116,  # 0113 -> 0116  Connectors MOD Input USB MOD Level
    115: 118,  # 0115 -> 0118  Connectors MOD Input DATA OFF MOD
    116: 119,  # 0116 -> 0119  Connectors MOD Input DATA MOD Cmd. Sub cmd.
    120: 125,  # 0120 -> 0125  Connectors USB SEND/Keying USB SEND DTR, 02=USB 
    121: 126,  # 0121 -> 0126  Connectors USB SEND/Keying USB Keying DTR, 02=US
    122: 127,  # 0122 -> 0127  Connectors USB SEND/Keying USB Keying DTR, 02=US
    124: 128,  # 0124 -> 0128  Connectors External Keypad VOICE
    125: 129,  # 0125 -> 0129  Connectors External Keypad KEYER
    126: 130,  # 0126 -> 0130  Connectors External Keypad RTTY
    127: 131,  # 0127 -> 0131  Connectors CI-V CI-V Transceive
    130: 132,  # 0130 -> 0132  Connectors CI-V CI-V USB Echo Back
    132: 133,  # 0132 -> 0133  Connectors CI-V USB/DATA Function USB Function
    134: 134,  # 0134 -> 0134  Connectors CI-V USB/DATA Function GPS Out)
    146: 73,  # 0146 -> 0073  Network Power OFF Setting
    152: 136,  # 0152 -> 0136  Display LCD Backlight
    155: 142,  # 0155 -> 0142  Display Meter Peak Hold
    156: 143,  # 0156 -> 0143  Display Memory Name
    160: 145,  # 0160 -> 0145  Display RX Call Sign Display
    161: 146,  # 0161 -> 0146  Display RX Position Indicator
    162: 147,  # 0162 -> 0147  Display RX Position Display, 02=ON) Cmd. Sub cmd
    163: 148,  # 0163 -> 0148  Display RX Position Display Timer
    164: 149,  # 0164 -> 0149  Display Reply Position Display
    165: 152,  # 0165 -> 0152  Display TX Call Sign Display
    166: 153,  # 0166 -> 0153  Display Scroll Speed
    168: 154,  # 0168 -> 0154  Display Opening Message
    169: 155,  # 0169 -> 0155  Display Power ON Check
    170: 156,  # 0170 -> 0156  Display Display Unit Latitude/Longitude
    171: 157,  # 0171 -> 0157  Display Display Unit Altitude/Distance
    172: 158,  # 0172 -> 0158  Display Display Unit Speed
    173: 159,  # 0173 -> 0159  Display Display Unit Temperature
    174: 160,  # 0174 -> 0160  Display Display Unit Barometric
    175: 161,  # 0175 -> 0161  Display Display Unit Rainfall
    176: 162,  # 0176 -> 0162  Display Display Unit Wind Speed
    177: 163,  # 0177 -> 0163  Display Display Language
    178: 164,  # 0178 -> 0164  Display System Language
    179: 165,  # 0179 -> 0165  20000101 ~ 20991231 Time Set Date/Time Date
    180: 166,  # 0180 -> 0166  Time Set Date/Time Time
    181: 167,  # 0181 -> 0167  Time Set Date/Time NTP Function
    182: 168,  # 0182 -> 0168  Time Set Date/Time NTP Server Address
    183: 169,  # 0183 -> 0169  Time Set Date/Time GPS Time Correct
    184: 170,  # 0184 -> 0170  Time Set UTC Offset
    185: 171,  # 0185 -> 0171  SD Card Import/Export CSV Format Separator/Decim
    186: 172,  # 0186 -> 0172  SD Card Import/Export CSV Format Date
    187: 173,  # 0187 -> 0173  SCOPE Scope during Tx
    188: 174,  # 0188 -> 0174  SCOPE Max Hold
    189: 175,  # 0189 -> 0175  SCOPE CENTER Type Display)
    190: 176,  # 0190 -> 0176  SCOPE Marker Position
    191: 177,  # 0191 -> 0177  SCOPE VBW
    192: 178,  # 0192 -> 0178  SCOPE Averaging
    193: 179,  # 0193 -> 0179  SCOPE Waveform Type
    194: 180,  # 0194 -> 0180  SCOPE Waveform Color
    195: 181,  # 0195 -> 0181  SCOPE Waveform Color
    196: 182,  # 0196 -> 0182  SCOPE Waveform Color
    197: 183,  # 0197 -> 0183  SCOPE Waterfall Display Cmd. Sub cmd.
    198: 184,  # 0198 -> 0184  SCOPE Waterfall Speed
    199: 185,  # 0199 -> 0185  SCOPE Waterfall Size
    200: 186,  # 0200 -> 0186  SCOPE Waterfall Peak Color Level
    201: 187,  # 0201 -> 0187  SCOPE Waterfall Marker Auto-hide
    211: 239,  # 0211 -> 0239  AUDIO SCOPE FFT Scope Waveform Type
    212: 240,  # 0212 -> 0240  AUDIO SCOPE FFT Scope Waveform Color
    213: 241,  # 0213 -> 0241  AUDIO SCOPE FFT Scope Waterfall Display
    214: 242,  # 0214 -> 0242  AUDIO SCOPE Oscilloscope Waveform Color
    215: 243,  # 0215 -> 0243  VOICE TX TX LEVEL
    216: 244,  # 0216 -> 0244  VOICE TX Auto Monitor
    217: 245,  # 0217 -> 0245  VOICE TX Repeat Time
    218: 246,  # 0218 -> 0246  KEYER 001 Number Style
    219: 247,  # 0219 -> 0247  KEYER 001 Count Up Trigger 0220 0001 to 9999 KEY
    220: 248,  # 0220 -> 0248  KEYER 001 Present Number
    221: 249,  # 0221 -> 0249  CW-KEY Side Tone Level
    222: 250,  # 0222 -> 0250  CW-KEY Side Tone Level Limit
    223: 251,  # 0223 -> 0251  CW-KEY Keyer Repeat time
    224: 252,  # 0224 -> 0252  CW-KEY Dot/Dash Ratio
    225: 253,  # 0225 -> 0253  CW-KEY Rise Time
    226: 254,  # 0226 -> 0254  CW-KEY Paddle Polarity
    227: 255,  # 0227 -> 0255  CW-KEY Key Type
    228: 256,  # 0228 -> 0256  CW-KEY MIC Up/Down Keyer
    229: 257,  # 0229 -> 0257  RTTY DECODE FFT Scope Averaging
    230: 258,  # 0230 -> 0258  RTTY DECODE FFT Scope Waveform Color
    231: 259,  # 0231 -> 0259  RTTY DECODE Decode USOS
    232: 260,  # 0232 -> 0260  RTTY DECODE Decode New Line Code
    233: 261,  # 0233 -> 0261  RTTY DECODE TX USOS
    235: 262,  # 0235 -> 0262  RTTY DECODE Font Color
    236: 263,  # 0236 -> 0263  RTTY DECODE Font Color
    237: 264,  # 0237 -> 0264  RTTY DECODE LOG Decode Log
    238: 265,  # 0238 -> 0265  RTTY DECODE LOG Log Set File Type Cmd. Sub cmd.
    239: 266,  # 0239 -> 0266  RTTY DECODE Log Set Time Stamp
    240: 267,  # 0240 -> 0267  RTTY DECODE Log Set Time Stamp
    241: 268,  # 0241 -> 0268  RTTY DECODE Log Set Time Stamp
    242: 269,  # 0242 -> 0269  QSO RECORDER Recorder Set TX REC Audio
    243: 270,  # 0243 -> 0270  QSO RECORDER Recorder Set RX REC Condition
    244: 271,  # 0244 -> 0271  QSO RECORDER Recorder Set File Split
    246: 272,  # 0246 -> 0272  QSO RECORDER Recorder Set PTT Auto REC
    247: 273,  # 0247 -> 0273  QSO RECORDER Recorder Set PRE-REC for PTT Auto R
    248: 274,  # 0248 -> 0274  QSO RECORDER Player Set Skip Time
    249: 275,  # 0249 -> 0275  SCAN SCAN Speed
    250: 276,  # 0250 -> 0276  SCAN SCAN Resume
    251: 277,  # 0251 -> 0277  SCAN Pause Timer
    252: 278,  # 0252 -> 0278  SCAN Resume Timer
    253: 279,  # 0253 -> 0279  SCAN Temporary Skip Timer
    254: 280,  # 0254 -> 0280  SCAN MAIN DIAL Operation
    255: 281,  # 0255 -> 0281  GPS GPS Set GPS Select
    257: 286,  # 0257 -> 0286  GPS GPS Set Manual Position
    258: 287,  # 0258 -> 0287  GPS GPS TX Mode
    259: 288,  # 0259 -> 0288  GPS GPS TX Mode D-PRS Unproto Address
    260: 289,  # 0260 -> 0289  GPS GPS TX Mode D-PRS TX Format
    261: 290,  # 0261 -> 0290  GPS GPS TX Mode D-PRS TX Format Position Symbol
    262: 291,  # 0262 -> 0291  GPS GPS TX Mode D-PRS TX Format Position the GPS
    263: 292,  # 0263 -> 0292  GPS GPS TX Mode D-PRS TX Format Position the GPS
    264: 293,  # 0264 -> 0293  GPS GPS TX Mode D-PRS TX Format Position the GPS
    265: 294,  # 0265 -> 0294  GPS GPS TX Mode D-PRS TX Format Position the GPS
    266: 295,  # 0266 -> 0295  GPS GPS TX Mode D-PRS TX Format Position SSID, 0
    267: 296,  # 0267 -> 0296  GPS GPS TX Mode D-PRS TX Format Position Comment
    268: 297,  # 0268 -> 0297  GPS GPS TX Mode D-PRS TX Format Position Comment
    269: 298,  # 0269 -> 0298  GPS GPS TX Mode D-PRS TX Format Position Comment
    270: 299,  # 0270 -> 0299  GPS GPS TX Mode D-PRS TX Format Position Comment
    271: 300,  # 0271 -> 0300  GPS GPS TX Mode D-PRS TX Format Position Comment
    272: 301,  # 0272 -> 0301  GPS GPS TX Mode D-PRS TX Format Position Time St
    273: 302,  # 0273 -> 0302  GPS GPS TX Mode D-PRS TX Format Position Altitud
    274: 303,  # 0274 -> 0303  GPS GPS TX Mode D-PRS TX Format Position Data Ex
    275: 304,  # 0275 -> 0304  GPS GPS TX Mode D-PRS TX Format Position Power
    276: 305,  # 0276 -> 0305  GPS GPS TX Mode D-PRS TX Format Position Height,
    277: 306,  # 0277 -> 0306  GPS GPS TX Mode D-PRS TX Format Position Gain
    278: 307,  # 0278 -> 0307  GPS GPS TX Mode D-PRS TX Format Position Directi
    279: 308,  # 0279 -> 0308  GPS GPS TX Mode D-PRS TX Format Object Object Na
    280: 309,  # 0280 -> 0309  GPS GPS TX Mode D-PRS TX Format Object Data Type
    281: 310,  # 0281 -> 0310  GPS GPS TX Mode D-PRS TX Format Object Symbol
    282: 311,  # 0282 -> 0311  GPS GPS TX Mode D-PRS TX Format Object Comment
    283: 312,  # 0283 -> 0312  GPS GPS TX Mode D-PRS TX Format Object Position
    284: 313,  # 0284 -> 0313  GPS GPS TX Mode D-PRS TX Format Object Data Exte
    285: 314,  # 0285 -> 0314  000 to 360 GPS GPS TX Mode D-PRS TX Format Objec
    286: 315,  # 0286 -> 0315  50 GPS GPS TX Mode D-PRS TX Format Object Speed
    287: 316,  # 0287 -> 0316  GPS GPS TX Mode D-PRS TX Format Object Power
    288: 317,  # 0288 -> 0317  GPS GPS TX Mode D-PRS TX Format Object Height, 0
    289: 318,  # 0289 -> 0318  GPS GPS TX Mode D-PRS TX Format Object Gain
    290: 319,  # 0290 -> 0319  GPS GPS TX Mode D-PRS TX Format Object Directivi
    291: 320,  # 0291 -> 0320  GPS GPS TX Mode D-PRS TX Format Object SSID, 02=
    292: 321,  # 0292 -> 0321  GPS GPS TX Mode D-PRS TX Format Object Time Stam
    293: 322,  # 0293 -> 0322  GPS GPS TX Mode D-PRS TX Format Item Item Name
    294: 323,  # 0294 -> 0323  GPS GPS TX Mode D-PRS TX Format Item Data Type
    295: 324,  # 0295 -> 0324  GPS GPS TX Mode D-PRS TX Format Item Symbol
    296: 325,  # 0296 -> 0325  GPS GPS TX Mode D-PRS TX Format Item Comment
    297: 326,  # 0297 -> 0326  GPS GPS TX Mode D-PRS TX Format Item Position
    298: 327,  # 0298 -> 0327  GPS GPS TX Mode D-PRS TX Format Item Data Extens
    299: 328,  # 0299 -> 0328  000 to 360 GPS GPS TX Mode D-PRS TX Format Item 
    300: 329,  # 0300 -> 0329  50 GPS GPS TX Mode D-PRS TX Format Item Speed
    301: 330,  # 0301 -> 0330  GPS GPS TX Mode D-PRS TX Format Item Power
    302: 331,  # 0302 -> 0331  GPS GPS TX Mode D-PRS TX Format Item Height, 01=
    303: 332,  # 0303 -> 0332  GPS GPS TX Mode D-PRS TX Format Item Gain
    304: 333,  # 0304 -> 0333  GPS GPS TX Mode D-PRS TX Format Item Directivity
    305: 334,  # 0305 -> 0334  GPS GPS TX Mode D-PRS TX Format Item SSID, 02=-1
    306: 335,  # 0306 -> 0335  GPS GPS TX Mode D-PRS TX Format Weather Symbol
    307: 336,  # 0307 -> 0336  GPS GPS TX Mode D-PRS TX Format Weather SSID, 02
    308: 337,  # 0308 -> 0337  GPS GPS TX Mode D-PRS TX Format Weather Comment
    309: 338,  # 0309 -> 0338  GPS GPS TX Mode D-PRS TX Format Weather Time Sta
    310: 339,  # 0310 -> 0339  *6 GPS GPS TX Mode NMEA GPS Sentence
    311: 340,  # 0311 -> 0340  *6 GPS GPS TX Mode NMEA GPS Sentence
    312: 341,  # 0312 -> 0341  *6 GPS GPS TX Mode NMEA GPS Sentence
    313: 342,  # 0313 -> 0342  *6 GPS GPS TX Mode NMEA GPS Sentence
    314: 343,  # 0314 -> 0343  *6 GPS GPS TX Mode NMEA GPS Sentence
    315: 344,  # 0315 -> 0344  GPS TX Mode NMEA GPS Sentence (GSV)
    316: 345,  # 0316 -> 0345  GPS GPS TX Mode NMEA GPS Message
    317: 346,  # 0317 -> 0346  GPS GPS Alarm> Alarm Area
    318: 347,  # 0318 -> 0347  GPS GPS Alarm> Alarm Area
    319: 354,  # 0319 -> 0354  GPS Auto TX
    320: 355,  # 0320 -> 0355  DTMF Speed
    321: 356,  # 0321 -> 0356  Set the NB LEVEL (144 MHz)
    322: 357,  # 0322 -> 0357  Set the NB DEPTH (144 MHz)
    323: 358,  # 0323 -> 0358  Set the NB WIDTH
    324: 356,  # 0324 -> 0356  Set the NB LEVEL (430 MHz)
    325: 357,  # 0325 -> 0357  Set the NB DEPTH (430 MHz)
    326: 358,  # 0326 -> 0358  Set the NB WIDTH
    330: 359,  # 0330 -> 0359  Set the VOX DELAY
    331: 360,  # 0331 -> 0360  Set the VOX voice delay
    338: 361,  # 0338 -> 0361  Set the Received Call sign Display
    339: 362,  # 0339 -> 0362  Set the Compass Direction
}

# IC-705 1A 05 items whose data range is 0000 ~ 0255
# (2-byte BCD level value, 0000=min .. 0255=max).
# NOTE: 0089 REF Adjust also takes a 2-byte BCD value but its range is 0000 ~ 0511.
IC705_BCD_ITEMS = {
    29, 61, 102, 110, 113, 116,
    117, 136, 243, 249, 356, 358,
}
