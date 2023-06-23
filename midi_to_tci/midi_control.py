from enum import IntEnum
from functools import partial
from bisect import bisect_right, bisect_left
from eesdr_tci import tci
from eesdr_tci.Listener import Listener
from eesdr_tci.tci import TciCommandSendAction
from config import Config
import mido
import asyncio

class MIDI(IntEnum):
    KEYUP = 0
    ENCDOWN = 21
    CLICK = 63
    ENCUP = 105
    KEYDOWN = 127

class CC(IntEnum):
    ENC_LARGE = 20
    ENC_SMALL_LEFT = 21
    ENC_SMALL_RIGHT = 22
    KEY_R1_C1 = 102
    KEY_R1_C2 = 103
    KEY_R1_C3 = 104
    KEY_R1_C4 = 105
    KEY_R2_C1 = 106
    KEY_R2_C2 = 107
    KEY_R2_C3 = 108
    KEY_R2_C4 = 109
    KEY_R3_C1 = 110
    KEY_R3_C2 = 111
    KEY_R3_C3 = 112
    KEY_R3_C4 = 113
    KEY_R4_C1 = 114
    KEY_R4_C2 = 115
    KEY_R4_C3 = 116
    KEY_R4_C4 = 117

class MODS:
    UI_LIST = ["AM", "LSB", "USB", "CW", "NFM", "DIGL", "DIGU", "WFM"]
    UI_LIST_MAX = len(UI_LIST) - 1
    DEFAULT_LEFT  = {"AM": -3000, "LSB": -3000, "USB":   25, "CW": -250, "NFM": -6000, "DIGL": -3000, "DIGU":   25, "WFM": -24000}
    DEFAULT_RIGHT = {"AM":  3000, "LSB":   -25, "USB": 3000, "CW":  250, "NFM":  6000, "DIGL":   -25, "DIGU": 3000, "WFM":  24000}
    WHEEL_LEFT  = {"AM": -25, "LSB": -25, "USB":  0, "CW": -25, "NFM": -25, "DIGL": -25, "DIGU":  0, "WFM": -250}
    WHEEL_RIGHT = {"AM":  25, "LSB":   0, "USB": 25, "CW":  25, "NFM":  25, "DIGL":   0, "DIGU": 25, "WFM":  250}

class KNOBPLANE(IntEnum):
    BASE = 0
    FILTER = 1
    MOD = 2
    BAND = 3
    DRIVE = 4
    VOLUME = 5
    MONITOR = 6

class FILTERSIDE(IntEnum):
    LEFT  = -1
    MAIN  =  0
    RIGHT =  1

class Band:
    def __init__(self, name, min_freq, max_freq, seg1=None, seg2=None):
        self.name = name
        self.min_freq = min_freq * 1000
        self.max_freq = max_freq * 1000
        if seg1 is None:
            self.seg1_freq = (self.min_freq + self.max_freq) / 2
            self.seg2_freq = None
        else:
            self.seg1_freq = seg1 * 1000

        if seg2 is None:
            self.seg2_freq = None
        else:
            self.seg2_freq = seg2 * 1000

    def in_band(self, freq):
        return freq >= self.min_freq and freq <= self.max_freq

    def points(self):
        if self.seg2_freq == None:
            return [self.seg1_freq]
        else:
            return [self.seg1_freq, self.seg2_freq]

class BANDS:
    INFO = [ Band("160m", 1800, 2000), 
             Band("80m", 3500, 4000, 3525, 3800), 
             Band("60m", 5330.5, 5407.5, 5358.5), 
             Band("40m", 7000, 7300, 7025, 7175),
             Band("30m", 10100, 10150),
             Band("20m", 14000, 14350, 14025, 14225),
             Band("17m", 18068, 18168, 18110),
             Band("15m", 21000, 21450, 21025, 21275),
             Band("12m", 24890, 24990, 24930),
             Band("10m", 28000, 29700, 28300, 29000),
             Band("6m", 50000, 54000, 50100, 52000),
             Band("2m", 144000, 148000, 144100, 147000),
           ]
    NAMES = [band.name for band in INFO]
    POINTS = [i for j in [band.points() for band in INFO] for i in j]

    def FreqBand(freq):
        chk = [band.in_band(freq) for band in INFO]
        if not any(chk):
            return None
        else:
            return INFO[chk.index(True)]

params_dict = {}

async def update_params(name, rx, subrx, params):
    global params_dict

    print("TCI", name, rx, subrx, params)
    if rx not in params_dict:
        params_dict[rx] = {}
    if subrx not in params_dict[rx]:
        params_dict[rx][subrx] = {}
    params_dict[rx][subrx][name] = params

def get_param(name, rx = None, subrx = None):
    global params_dict
    cmd = tci.COMMANDS[name]
    if not cmd.has_rx:
        rx = None
    if not cmd.has_sub_rx:
        subrx = None
    return params_dict[rx][subrx][name]

def do_band_scroll(val, rx, subrx):
    rx_dds = get_param("DDS", rx, subrx)
    subrx_if = get_param("IF", rx, subrx)
    curr_freq = rx_dds + subrx_if
    if val == MIDI.ENCDOWN:
        idx = bisect_left(BANDS.POINTS, curr_freq) - 1
    elif val == MIDI.ENCUP:
        idx = bisect_right(BANDS.POINTS, curr_freq)
    else:
        return []

    if idx >= len(BANDS.POINTS):
        idx = 0
    rx_dds = BANDS.POINTS[idx]
    print(idx,rx_dds)
    subrx_if = 0

    return [ tci.COMMANDS["DDS"].prepare_string(TciCommandSendAction.WRITE, rx=rx, params=[int(rx_dds)]),
             tci.COMMANDS["IF"].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[int(subrx_if)]) ]

def do_freq_scroll(incr, val, rx, subrx):
    rx_dds = get_param("DDS", rx, subrx)
    subrx_if = get_param("IF", rx, subrx)
    subrx0_if = get_param("IF", rx, 0)
    if_lims = get_param("IF_LIMITS")

    if val == MIDI.CLICK:
        if subrx == 0:
            rx_dds = rx_dds + subrx_if
            subrx_if = 0
        else:
            subrx_if = subrx0_if
    elif val == MIDI.ENCDOWN:
        subrx_if -= incr
    elif val == MIDI.ENCUP:
        subrx_if += incr
    else:
        return []

    if subrx_if < if_lims[0]:
        subrx_if = if_lims[0]
    if subrx_if > if_lims[1]:
        subrx_if = if_lims[1]

    return [ tci.COMMANDS["DDS"].prepare_string(TciCommandSendAction.WRITE, rx=rx, params=[int(rx_dds)]),
             tci.COMMANDS["IF"].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[int(subrx_if)]) ]

def do_filter_scroll(side, val, rx, subrx):
    flt = get_param("RX_FILTER_BAND", rx, subrx)
    mod = get_param("MODULATION", rx, subrx)

    if val == MIDI.CLICK:
        if side == FILTERSIDE.LEFT or side == FILTERSIDE.MAIN:
            flt[0] = MODS.DEFAULT_LEFT[mod]
        if side == FILTERSIDE.RIGHT or side == FILTERSIDE.MAIN:
            flt[0] = MODS.DEFAULT_RIGHT[mod]
    elif val == MIDI.ENCDOWN:
        if side == FILTERSIDE.LEFT:
            flt[0] -= 25
        if side == FILTERSIDE.MAIN:
            flt[0] -= MODS.WHEEL_LEFT[mod]
            flt[1] -= MODS.WHEEL_RIGHT[mod]
        if side == FILTERSIDE.RIGHT:
            flt[1] -= 25
    elif val == MIDI.ENCUP:
        if side == FILTERSIDE.LEFT:
            flt[0] += 25
        if side == FILTERSIDE.MAIN:
            flt[0] += MODS.WHEEL_LEFT[mod]
            flt[1] += MODS.WHEEL_RIGHT[mod]
        if side == FILTERSIDE.RIGHT:
            flt[1] += 25
    else:
        return []

    return [ tci.COMMANDS["RX_FILTER_BAND"].prepare_string(TciCommandSendAction.WRITE, rx=rx, params=flt) ]

def do_mod_scroll(val, rx, subrx):
    # mod_list = get_param("MODULATIONS_LIST")
    # There are many modulations exposed in this list that aren't in the interface
    # The list included in the MODS constant matches the EESDR v3 beta interface for obvious scroll order
    mod = get_param("MODULATION", rx, subrx)
    midx = MODS.UI_LIST.index(mod)

    if val == MIDI.ENCDOWN:
        midx -= 1
        if midx < 0:
            midx = MODS.UI_LIST_MAX
    elif val == MIDI.ENCUP:
        midx += 1
        if midx > MODS.UI_LIST_MAX:
            midx = 0
    else:
        return []

    return [ tci.COMMANDS["MODULATION"].prepare_string(TciCommandSendAction.WRITE, rx=rx, params=[MODS.UI_LIST[midx]]) ]
                    
def do_enable_toggle(val, rx, subrx):
    if val == MIDI.CLICK:
        if rx > 0 and subrx == 0:
            return do_toggle("RX_ENABLE", MIDI.KEYDOWN, rx, subrx)
        elif subrx > 0:
            return do_toggle("RX_CHANNEL_ENABLE", MIDI.KEYDOWN, rx, subrx)
    else:
        return []

def do_toggle(name, val, rx, subrx):
    if val == MIDI.KEYDOWN or val == MIDI.CLICK:
        cv = not get_param(name, rx, subrx)
        return [ tci.COMMANDS[name].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[cv]) ]
    else:
        return []

def do_momentary(name, val, rx, subrx):
    cv = (val == MIDI.KEYDOWN)
    return [ tci.COMMANDS[name].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[cv]) ]

def do_generic_scroll(name, incr, val, rx, subrx):
    cv = get_param(name, rx, subrx)

    if val == MIDI.ENCDOWN:
        cv -= incr
    elif val == MIDI.ENCUP:
        cv += incr
    else:
        return []

    return [ tci.COMMANDS[name].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[cv]) ]

def do_generic_set(name, sp, val, rx, subrx):
    if val == MIDI.KEYDOWN or val == MIDI.CLICK:
        return [ tci.COMMANDS[name].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[sp]) ]
    else:
        return []

def do_volume_reset(val, rx, subrx):
    if val == MIDI.KEYDOWN or val == MIDI.CLICK:
        return [ tci.COMMANDS["RX_BALANCE"].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[0]),
                 tci.COMMANDS["RX_VOLUME"].prepare_string(TciCommandSendAction.WRITE, rx=rx, sub_rx=subrx, params=[0]) ]
    else:
        return []

async def run_cmds(tci_listener, cmds):
    for c in cmds:
        await tci_listener.send(c)

def midi_stream():
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    def callback(msg):
        loop.call_soon_threadsafe(queue.put_nowait, msg)
    async def stream():
        while True:
            yield await queue.get()
    return callback, stream()

async def midi_rx(tci_listener, midi_port):
    global params_dict

    curr_subrx = 0
    curr_rx = 0
    knob_plane = 0

    cb, stream = midi_stream()
    mido.open_input(midi_port, callback=cb)

    async for msg in stream:
        if not msg.is_cc():
            continue

        print("MIDI", msg)

        # Knob Plane Toggles
        kp_map = { CC.KEY_R1_C1: KNOBPLANE.FILTER,
                   CC.KEY_R1_C2: KNOBPLANE.MOD,
                   CC.KEY_R1_C3: KNOBPLANE.BAND,
                   CC.KEY_R1_C4: KNOBPLANE.DRIVE,
                   CC.KEY_R4_C1: KNOBPLANE.VOLUME,
                   CC.KEY_R4_C2: KNOBPLANE.MONITOR,
                 }

        # Keypress TCI Toggles/Momentaries
        key_map = { CC.KEY_R2_C1: partial(do_toggle, "SPLIT_ENABLE"),
                    CC.KEY_R2_C4: partial(do_toggle, "RX_APF_ENABLE"),
                    CC.KEY_R3_C1: partial(do_toggle, "RX_NB_ENABLE"),
                    CC.KEY_R3_C2: partial(do_toggle, "RX_BIN_ENABLE"),
                    CC.KEY_R3_C3: partial(do_toggle, "RX_NR_ENABLE"),
                    CC.KEY_R3_C4: partial(do_toggle, "RX_ANC_ENABLE"),
                    CC.KEY_R4_C3: partial(do_momentary, "TRX"),
                    CC.KEY_R4_C4: partial(do_momentary, "TUNE"),
                  }

        # Knob Clicks
        knob_click_map = { CC.ENC_LARGE: [ partial(do_freq_scroll, 250),
                                           partial(do_filter_scroll, FILTERSIDE.MAIN),
                                           None,
                                           None,
                                           partial(do_generic_set, "DRIVE", 50),
                                           partial(do_toggle, "MUTE"),
                                           partial(do_toggle, "MON_ENABLE"),
                                         ],
                           CC.ENC_SMALL_LEFT: [ do_enable_toggle,
                                                partial(do_filter_scroll, FILTERSIDE.LEFT),
                                                partial(do_toggle, "RIT_ENABLE"),
                                                None,
                                                None,
                                                do_volume_reset,
                                                None,
                                              ],
                           CC.ENC_SMALL_RIGHT: [ partial(do_toggle, "SQL_ENABLE"),
                                                 partial(do_filter_scroll, FILTERSIDE.RIGHT),
                                                 partial(do_toggle, "XIT_ENABLE"),
                                                 None,
                                                 partial(do_generic_set, "TUNE_DRIVE", 10),
                                                 partial(do_toggle, "RX_MUTE"),
                                                 None,
                                               ],
                         }

        # Knob Scrolls
        knob_scroll_map = { CC.ENC_LARGE: [ partial(do_freq_scroll, 250),
                                            partial(do_filter_scroll, FILTERSIDE.MAIN),
                                            do_mod_scroll,
                                            do_band_scroll,
                                            partial(do_generic_scroll, "DRIVE", 2),
                                            partial(do_generic_scroll, "VOLUME", 2),
                                            partial(do_generic_scroll, "MON_VOLUME", 2),
                                          ],
                            CC.ENC_SMALL_LEFT: [ partial(do_freq_scroll, 2500),
                                                 partial(do_filter_scroll, FILTERSIDE.LEFT),
                                                 partial(do_generic_scroll, "RIT_OFFSET", 25),
                                                 None,
                                                 None,
                                                 partial(do_generic_scroll, "RX_BALANCE", 2),
                                                 None,
                                               ],
                            CC.ENC_SMALL_RIGHT: [ partial(do_generic_scroll, "SQL_LEVEL", 1),
                                                  partial(do_filter_scroll, FILTERSIDE.RIGHT),
                                                  partial(do_generic_scroll, "XIT_OFFSET", 25),
                                                  None,
                                                  partial(do_generic_scroll, "TUNE_DRIVE", 2),
                                                  partial(do_generic_scroll, "RX_VOLUME", 2),
                                                  None,
                                                ],
                          }

        if msg.control in kp_map:
            knob_plane = kp_map[msg.control] if msg.value == MIDI.KEYDOWN else KNOBPLANE.BASE
        elif msg.control in key_map:
            await run_cmds(tci_listener, key_map[msg.control](msg.value, curr_rx, curr_subrx))
        elif msg.value == MIDI.CLICK and msg.control in knob_click_map:
            fn = knob_click_map[msg.control][knob_plane]
            if fn is not None:
                await run_cmds(tci_listener, fn(msg.value, curr_rx, curr_subrx))
        elif (msg.value == MIDI.ENCDOWN or msg.value == MIDI.ENCUP) and msg.control in knob_scroll_map:
            fn = knob_scroll_map[msg.control][knob_plane]
            if fn is not None:
                await run_cmds(tci_listener, fn(msg.value, curr_rx, curr_subrx))
        elif msg.control == CC.KEY_R2_C2:
                curr_subrx = 1 if msg.value == MIDI.KEYDOWN else 0
        elif msg.control == CC.KEY_R2_C3:
                curr_rx = 1 if msg.value == MIDI.KEYDOWN else 0

async def main(uri, midi_port):
    tci_listener = Listener(uri)

    tci_listener.add_param_listener("*", update_params)

    await tci_listener.start()
    await tci_listener.ready()

    asyncio.create_task(midi_rx(tci_listener, midi_port))

    await tci_listener.wait()

cfg = Config("config.json")
uri = cfg.get("uri", required=True)
midi_port = cfg.get("midi_port", required=True)

asyncio.run(main(uri, midi_port))
