import time
import wifi
import socketpool
import microcontroller
import json
import os

import adafruit_ntp
import rtc
from adafruit_datetime import datetime, timedelta

import mdns
import ipaddress
from adafruit_httpserver import Server, Request, Response, FileResponse, GET, POST
from adafruit_templateengine import render_template

import neopixel
import board
import analogio
from adafruit_pixel_framebuf import PixelFramebuffer

import traceback

class WordClock:

    def __init__(self):

        self.CREDENTIALS_FILE = "config.json"

        self.AP_SSID = f"WordClock_{microcontroller.cpu.uid.hex()}"
        self.BASE_HOSTNAME = "wordclock"

        self.pool = socketpool.SocketPool(wifi.radio)

        # show light
        self.is_light_allowed = True

        self.last_time_sync = 0
        self.sync_interval = 60 * 60 * 4 # 4 hours
        self.is_time_synced = False # is the time synced more than one time?


        self.disable_wifi_now = False
        self.wlan_off = True

        self.is_ap_started = False

        self.config = {"wifi": [], "color": {"r": 255, "g": 0, "b": 0}, "tz": 1, "auto_dst": True, "auto_brightness": True, "brightness": 1.0}
        
        self.pixels = neopixel.NeoPixel(board.IO15, 117, brightness=1, auto_write=False)
        self.pixels.fill((0, 0, 0))
        self.pixels.show()
        self.pixels_ignore = []
        
        self.hours_buffer = 0
        self.minutes_buffer = 0
        
        self.ldr = analogio.AnalogIn(board.IO11)        
        # make mean of LDR values
        self.ldr_count = 50
        self.ldr_values = [0] * self.ldr_count  # Initialize with 50 zeros
        
        self.pixel_framebuf = PixelFramebuffer(self.pixels, 11, 10, alternating=True)
        
        self.text_last_scroll = 0
        self.text_scroll_speed = 1000*1000*100
        self.text_x_offset = self.pixel_framebuf.width
        self.text_scroll_repeat = 0
        self.text_scroll_repeats = 2
        self.is_text_scroll = True
        self.is_client_connected = False
        
    def begin(self):
        # indicate clock is available and trying to connect..
        self.pixels[110] = self.get_color()
        self.pixels[112] = self.get_color()
        self.pixels[114] = self.get_color()
        self.pixels[116] = self.get_color()
        self.pixels.show()

        if not 'config.json' in os.listdir():
            self.write_config()
        self.read_config()
        print(self.config)
        self.init_server()
        self.start_wifi()

        # reset pixels
        self.pixels.fill((0, 0, 0))
        self.pixels.show()

    def loop(self):
        
        if self.is_text_scroll:
            self.set_brightness(1)
            self.scroll_text(str(wifi.radio.ipv4_address) if not self.is_ap_started else str(wifi.radio.ipv4_address_ap))

        now = time.localtime()

        # Sync time
        if wifi.radio.connected and (self.last_time_sync + self.sync_interval < time.time()) and not self.is_ap_started:
            self.adjust_time()

        # show clock
        if not self.is_text_scroll:
            if self.is_light_allowed:
                
                hours = now.tm_hour % 12
                minutes = now.tm_min

                if self.hours_buffer != hours or self.minutes_buffer != minutes:
                    self.display_time(hours, minutes)
                    self.hours_buffer = hours
                    self.minutes_buffer=minutes
                
                if self.config["auto_brightness"]:
                    self.adjust_brightness()

            else:
                self.disable_light()

        #Disable Wifi
        if self.disable_wifi_now:
            self.disable_wifi()

        # Poll Server
        if wifi.radio.enabled:
            try:
                if self.is_ap_started:
                    self.ap_server.poll()
                else:
                    self.server.poll()
            except Exception as e:
                print(f"Server error: {e}")
                self.writeLog("Server error")
                self.writeLog(e)

    def read_config(self):
        try:
            with open(self.CREDENTIALS_FILE, "r") as fp:
                self.config = json.load(fp)
        except OSError as e:
            print("Error when reading file")
            pass

    def write_config(self):
        try:
            with open(self.CREDENTIALS_FILE, "w") as fp:
                json.dump(self.config, fp)
        except OSError as e:
            print("Error when writing file")
            pass

    def register_mdns(self):
        self.mdns_server = mdns.Server(wifi.radio)

        hostname = self.BASE_HOSTNAME
        number = 0

        while True:
            try:
                self.mdns_server.hostname = hostname
                self.mdns_server.advertise_service(service_type="_http", protocol="_tcp", port=80)
                print(f"Registered mDNS: {hostname}.local")
                break
            except OSError:
                number += 1
                hostname = f"{self.BASE_HOSTNAME}{number}"
                print(f"Hostname {hostname}.local is taken, trying next...")

        return hostname

    def save_credentials(self, ssid, password):
        wifi_list = self.config.get("wifi", [])

        # Avoid saving duplicates
        if not any(entry["ssid"] == ssid for entry in wifi_list):
            wifi_list.append({"ssid": ssid, "password": password})
            self.config["wifi"] = wifi_list
            self.write_config()

    def connect_to_wifi(self):
        credentials = self.config.get("wifi", [])

        if not credentials:
            print("No saved Wi-Fi credentials found.")
            return False

        for entry in credentials:
            ssid = entry["ssid"]
            password = entry["password"]
            print(f"Trying to connect to {ssid}...")
            try:
                wifi.radio.connect(ssid, password, timeout=30)
                print(f"Connected to {ssid}!")
                return True
            except Exception as e:
                print(f"Failed to connect to {ssid}: {e}")
                self.writeLog(f"Failed to connect to {ssid}")
                self.writeLog(e)

        print("Could not connect to any saved network.")
        return False

    def start_access_point(self):
        print("Starting Access Point...")
        ipv4 = ipaddress.IPv4Address("192.168.251.1")
        netmask = ipaddress.IPv4Address("255.255.255.0")
        gateway = ipaddress.IPv4Address("192.168.251.254")
        wifi.radio.set_ipv4_address_ap(ipv4=ipv4, netmask=netmask, gateway=gateway)
        wifi.radio.start_ap(self.AP_SSID)
        self.is_ap_started = True
        print(f"AP started: {self.AP_SSID}")

    def scroll_text(self, text):
        
        if time.monotonic_ns() - self.text_last_scroll > self.text_scroll_speed:
            text_width = len(text) * 5
            self.pixel_framebuf.fill(0) 
            self.pixel_framebuf.text(text, self.text_x_offset, 2, 0xFF0000 if self.is_ap_started else 0x00FF00)
            self.pixel_framebuf.display()

            self.text_x_offset -= 1
            if self.text_x_offset < -(text_width+self.pixel_framebuf.width):
                self.text_x_offset = self.pixel_framebuf.width
                self.text_scroll_repeat += 1

            self.text_last_scroll = time.monotonic_ns()
            
            # Abort scrolling
            if not self.is_ap_started and self.text_scroll_repeat >= self.text_scroll_repeats:
                self.is_text_scroll = False

    def disable_light(self, show = True):
        if len(self.pixels_ignore) > 0:
            for i in range(len(self.pixels)):
                if not i in self.pixels_ignore:
                    self.pixels[i] = (0,0,0)
        else:
            self.pixels.fill((0,0,0))
        
        if show:
            self.pixels.show()

    def get_last_sunday(self, year, month):
        # Determine the first day of the next month
        if month == 12:
            next_month = datetime(year + 1, 1, 1)
        else:
            next_month = datetime(year, month + 1, 1)
        
        # Last day of current month:
        last_day = next_month - timedelta(days=1)
        
        # Walk backwards until we find a Sunday (weekday() returns 0=Monday, ... 6=Sunday)
        while last_day.weekday() != 6:
            last_day = last_day - timedelta(days=1)
        
        return last_day

        
    def is_daylight_saving_time(self):
        year = time.localtime().tm_year

        # Compute DST start boundary (Last Sunday in March at 02:00 local time)
        last_sun_march = self.get_last_sunday(year, 3)
        dst_start = datetime(year, 3, last_sun_march.day, 2, 0, 0)
        
        # Compute DST end boundary (Last Sunday in October at 03:00 local time)
        last_sun_oct = self.get_last_sunday(year, 10)
        dst_end = datetime(year, 10, last_sun_oct.day, 3, 0, 0)

        now = time.localtime()
        dt_local = datetime(now.tm_year,now.tm_mon,now.tm_mday,now.tm_hour,now.tm_min,now.tm_sec)
        
        return dst_start <= dt_local < dst_end

    def adjust_time(self):
        try:
            print(time.localtime())
            offset = self.config['tz']
            if self.config["auto_dst"] and self.is_daylight_saving_time():
                print("daylight saving time")
                offset = offset + 1
            print("try to sync time")
            ntp = adafruit_ntp.NTP(self.pool, tz_offset=offset)
            rtc.RTC().datetime = ntp.datetime
            print(time.localtime())

            # Only store last timestamp if one successfully sync (to retrigger a sync after first boot)
            if self.is_time_synced:
                print("Time was synced after boot")
                self.last_time_sync = time.time()
            else:
                print("First sync after boot")
                self.is_time_synced = True
                self.adjust_time()
        except Exception as e:
            print("Error syncing time")
            print(e)
            self.writeLog("Error syncing time")
            self.writeLog(e)
            pass

    def start_wifi(self):
        wifi.radio.enabled = True
        if self.connect_to_wifi():
            print("Wi-Fi connected. IP:", wifi.radio.ipv4_address)
            self.server.start(str(wifi.radio.ipv4_address), 80)
            print(f"Connect to WiFi, visit http://{wifi.radio.ipv4_address} to configure everything.")
        else:
            self.start_access_point()
            print("Wi-Fi connected. IP:", wifi.radio.ipv4_address_ap)
            self.ap_server.start(str(wifi.radio.ipv4_address_ap), 80)
            print(f"Connect to AP and visit http://{wifi.radio.ipv4_address_ap} to configure Wi-Fi.")

        time.sleep(5)
        self.register_mdns()

    def disable_wifi(self):
        print("disable wifi")
        self.disable_wifi_now = False
        self.server.stop()
        wifi.radio.enabled = False

    def adjust_brightness(self):
        ldr_brightness = 0
        
        # Append new value and keep only the last 50
        self.ldr_values.append(self.ldr.value)
        self.ldr_values = self.ldr_values[-self.ldr_count:]  # Trim to last 50

        # Calculate the average brightness
        ldr_brightness = sum(self.ldr_values) / self.ldr_count
        
        #print(ldr_brightness)

        if ldr_brightness < 320:
            brightness = 0.05
        elif ldr_brightness < 480:
            brightness = 0.1
        elif ldr_brightness < 1600:
            brightness = 0.2
        elif ldr_brightness < 6400:
            brightness = 0.3
        elif ldr_brightness < 16000:
            brightness = 0.5
        elif ldr_brightness < 24000:
            brightness = 0.7
        else:
            brightness = 1.0

        self.set_brightness(brightness)
    
    def set_brightness(self, brightness):
        self.pixels.brightness = brightness
        self.pixels.show()    
    
    def get_color(self):
        return (int(self.config['color']['r']), int(self.config['color']['g']), int(self.config['color']['b']))

    def get_pixels(self):
        return self.pixels
    
    def set_pixels_ignore(self, pixels_ignore = []):
        self.pixels_ignore = pixels_ignore

    def display_time(self, hours, minutes):
        
        # alles resetten
        self.disable_light(True)
        
        # ES
        self.pixels[0] = self.get_color()
        self.pixels[1] = self.get_color()
        # IST
        self.pixels[3] = self.get_color()
        self.pixels[4] = self.get_color()
        self.pixels[5] = self.get_color()
        
        # <5 Minutes
        if minutes%5 == 1:
            self.pixels[110] = self.get_color()
        if minutes%5 == 2:
            self.pixels[110] = self.get_color()
            self.pixels[112] = self.get_color()
        if minutes%5 == 3:
            self.pixels[110] = self.get_color()
            self.pixels[112] = self.get_color()
            self.pixels[114] = self.get_color()
        if minutes%5 == 4:
            self.pixels[110] = self.get_color()
            self.pixels[112] = self.get_color()
            self.pixels[114] = self.get_color()
            self.pixels[116] = self.get_color()
            
        # Minuten anzeige
        if minutes < 5:
            pass
        elif minutes < 10:
            # Fuenf
            self.pixels[7] = self.get_color()
            self.pixels[8] = self.get_color()
            self.pixels[9] = self.get_color()
            self.pixels[10] = self.get_color()
            # Nach
            self.pixels[38] = self.get_color()
            self.pixels[39] = self.get_color()
            self.pixels[40] = self.get_color()
            self.pixels[41] = self.get_color()
        elif minutes < 15:
            # Zehn
            self.pixels[18] = self.get_color()
            self.pixels[19] = self.get_color()
            self.pixels[20] = self.get_color()
            self.pixels[21] = self.get_color()
            # Nach
            self.pixels[38] = self.get_color()
            self.pixels[39] = self.get_color()
            self.pixels[40] = self.get_color()
            self.pixels[41] = self.get_color()
        elif minutes < 20:
            # Viertel
            self.pixels[26] = self.get_color()
            self.pixels[27] = self.get_color()
            self.pixels[28] = self.get_color()
            self.pixels[29] = self.get_color()
            self.pixels[30] = self.get_color()
            self.pixels[31] = self.get_color()
            self.pixels[32] = self.get_color()
            # Nach
            '''
            self.pixels[38] = self.get_color()
            self.pixels[39] = self.get_color()
            self.pixels[40] = self.get_color()
            self.pixels[41] = self.get_color()
            '''
        elif minutes < 25:
            # Zwanzig
            '''
            self.pixels[11] = self.get_color()
            self.pixels[12] = self.get_color()
            self.pixels[13] = self.get_color()
            self.pixels[14] = self.get_color()
            self.pixels[15] = self.get_color()
            self.pixels[16] = self.get_color()
            self.pixels[17] = self.get_color()
            '''
            # Nach
            '''
            self.pixels[38] = self.get_color()
            self.pixels[39] = self.get_color()
            self.pixels[40] = self.get_color()
            self.pixels[41] = self.get_color()
            '''
            # Zehn
            self.pixels[18] = self.get_color()
            self.pixels[19] = self.get_color()
            self.pixels[20] = self.get_color()
            self.pixels[21] = self.get_color()
            # Vor
            self.pixels[35] = self.get_color()
            self.pixels[36] = self.get_color()
            self.pixels[37] = self.get_color()
            # Halb
            self.pixels[44] = self.get_color()
            self.pixels[45] = self.get_color()
            self.pixels[46] = self.get_color()
            self.pixels[47] = self.get_color()
        elif minutes < 30:
            # Fuenf
            self.pixels[7] = self.get_color()
            self.pixels[8] = self.get_color()
            self.pixels[9] = self.get_color()
            self.pixels[10] = self.get_color()
            # Vor
            self.pixels[35] = self.get_color()
            self.pixels[36] = self.get_color()
            self.pixels[37] = self.get_color()
            # Halb
            self.pixels[44] = self.get_color()
            self.pixels[45] = self.get_color()
            self.pixels[46] = self.get_color()
            self.pixels[47] = self.get_color()
        elif minutes < 35:
            # Halb
            self.pixels[44] = self.get_color()
            self.pixels[45] = self.get_color()
            self.pixels[46] = self.get_color()
            self.pixels[47] = self.get_color()
        elif minutes < 40:
            # Fuenf
            self.pixels[7] = self.get_color()
            self.pixels[8] = self.get_color()
            self.pixels[9] = self.get_color()
            self.pixels[10] = self.get_color()
            # Nach
            self.pixels[38] = self.get_color()
            self.pixels[39] = self.get_color()
            self.pixels[40] = self.get_color()
            self.pixels[41] = self.get_color()
            # Halb
            self.pixels[44] = self.get_color()
            self.pixels[45] = self.get_color()
            self.pixels[46] = self.get_color()
            self.pixels[47] = self.get_color()
        elif minutes < 45:
            # Zwanzig
            '''
            self.pixels[11] = self.get_color()
            self.pixels[12] = self.get_color()
            self.pixels[13] = self.get_color()
            self.pixels[14] = self.get_color()
            self.pixels[15] = self.get_color()
            self.pixels[16] = self.get_color()
            self.pixels[17] = self.get_color()
            '''
            # Vor
            '''
            self.pixels[35] = self.get_color()
            self.pixels[36] = self.get_color()
            self.pixels[37] = self.get_color()
            '''
            # Zehn
            self.pixels[18] = self.get_color()
            self.pixels[19] = self.get_color()
            self.pixels[20] = self.get_color()
            self.pixels[21] = self.get_color()
            # Nach
            self.pixels[38] = self.get_color()
            self.pixels[39] = self.get_color()
            self.pixels[40] = self.get_color()
            self.pixels[41] = self.get_color()
            # Halb
            self.pixels[44] = self.get_color()
            self.pixels[45] = self.get_color()
            self.pixels[46] = self.get_color()
            self.pixels[47] = self.get_color()
        elif minutes < 50:
            # Drei
            self.pixels[22] = self.get_color()
            self.pixels[23] = self.get_color()
            self.pixels[24] = self.get_color()
            self.pixels[25] = self.get_color()
            # Viertel
            self.pixels[26] = self.get_color()
            self.pixels[27] = self.get_color()
            self.pixels[28] = self.get_color()
            self.pixels[29] = self.get_color()
            self.pixels[30] = self.get_color()
            self.pixels[31] = self.get_color()
            self.pixels[32] = self.get_color()
            # Vor
            '''
            self.pixels[35] = self.get_color()
            self.pixels[36] = self.get_color()
            self.pixels[37] = self.get_color()
            '''
        elif minutes < 55:
            # Zehn
            self.pixels[18] = self.get_color()
            self.pixels[19] = self.get_color()
            self.pixels[20] = self.get_color()
            self.pixels[21] = self.get_color()
            # Vor
            self.pixels[35] = self.get_color()
            self.pixels[36] = self.get_color()
            self.pixels[37] = self.get_color()
        else:
            # Fuenf
            self.pixels[7] = self.get_color()
            self.pixels[8] = self.get_color()
            self.pixels[9] = self.get_color()
            self.pixels[10] = self.get_color()
            # Vor
            self.pixels[35] = self.get_color()
            self.pixels[36] = self.get_color()
            self.pixels[37] = self.get_color()
        '''
        if minutes >= 25:
        hours = (hours + 1) % 12
        '''
        if minutes >= 15:
           hours = (hours + 1) % 12
        
        if hours == 0:
             # Zwoelf
            self.pixels[72] = self.get_color()
            self.pixels[73] = self.get_color()
            self.pixels[74] = self.get_color()
            self.pixels[75] = self.get_color()
            self.pixels[76] = self.get_color()
        elif hours == 1:
             # Ein(s)
            self.pixels[61] = self.get_color()
            self.pixels[62] = self.get_color()
            self.pixels[63] = self.get_color()
            if minutes >= 5:
                self.pixels[60] = self.get_color()
        elif hours == 2:
             # Zwei
            self.pixels[62] = self.get_color()
            self.pixels[63] = self.get_color()
            self.pixels[64] = self.get_color()
            self.pixels[65] = self.get_color()
        elif hours == 3:
            #Drei
            self.pixels[67] = self.get_color()
            self.pixels[68] = self.get_color()
            self.pixels[69] = self.get_color()
            self.pixels[70] = self.get_color()
        elif hours == 4:
            # Vier
            self.pixels[77] = self.get_color()
            self.pixels[78] = self.get_color()
            self.pixels[79] = self.get_color()
            self.pixels[80] = self.get_color()
        elif hours == 5:
            # Fuenf
            self.pixels[51] = self.get_color()
            self.pixels[52] = self.get_color()
            self.pixels[53] = self.get_color()
            self.pixels[54] = self.get_color()
        elif hours == 6:
            # Sechs
            self.pixels[104] = self.get_color()
            self.pixels[105] = self.get_color()
            self.pixels[106] = self.get_color()
            self.pixels[107] = self.get_color()
            self.pixels[108] = self.get_color()
        elif hours == 7:
            #Sieben
            self.pixels[55] = self.get_color()
            self.pixels[56] = self.get_color()
            self.pixels[57] = self.get_color()
            self.pixels[58] = self.get_color()
            self.pixels[59] = self.get_color()
            self.pixels[60] = self.get_color()
        elif hours == 8:
            # Acht
            self.pixels[89] = self.get_color()
            self.pixels[90] = self.get_color()
            self.pixels[91] = self.get_color()
            self.pixels[92] = self.get_color()
        elif hours == 9:
            # Neun
            self.pixels[81] = self.get_color()
            self.pixels[82] = self.get_color()
            self.pixels[83] = self.get_color()
            self.pixels[84] = self.get_color()
        elif hours == 10:
            # Zehn
            self.pixels[93] = self.get_color()
            self.pixels[94] = self.get_color()
            self.pixels[95] = self.get_color()
            self.pixels[96] = self.get_color()
        elif hours == 11:
            # Elf
            self.pixels[85] = self.get_color()
            self.pixels[86] = self.get_color()
            self.pixels[87] = self.get_color()
        
        # UHR
        if minutes < 5:
            self.pixels[101] = self.get_color()
            self.pixels[100] = self.get_color()
            self.pixels[99] = self.get_color()
     
        self.pixels.show()

    def writeLog(self, message):
        try:
            with open("/logfile2.txt", "a") as fp:
                if isinstance(message, Exception):
                    traceback.print_exception(None, message, message.__traceback__, -1, fp)
                else:
                    fp.write('{}\n'.format(message))
                    fp.flush()
        except OSError as e:  # Typically when the filesystem isn't writeable...
            print("Error when writing file")
            pass

    def init_server(self):
        self.ap_server = Server(self.pool, "/www/public")
        self.server = Server(self.pool, "/www/public")

        @self.ap_server.route("/", GET)
        def homepage(request: Request):
            self.is_client_connected = True
            networks = wifi.radio.start_scanning_networks()
            ssid_list = [net.ssid for net in networks]
            wifi.radio.stop_scanning_networks()

            return Response(
                request,
                render_template(
                    "www/templates/wifi.tpl.html",
                    context={"ssid_list": ssid_list},
                ),
                content_type="text/html"
            )

        @self.ap_server.route("/connect", POST)
        def connect(request: Request):
            data = request.json()
            ssid = data.get("ssid")
            password = data.get("password")
            self.save_credentials(ssid, password)
            print(f"Credentials saved: {ssid}")
            time.sleep(2)
            microcontroller.reset()

        @self.server.route("/", GET)
        def homepage2(request: Request):

            return Response(
                request,
                render_template(
                    "www/templates/index.tpl.html",
                    context={"id": microcontroller.cpu.uid.hex(), "config": self.config, "color": f"#{self.config['color']['r']:02x}{self.config['color']['g']:02x}{self.config['color']['b']:02x}"},
                ),
                content_type="text/html",
            )

        @self.server.route("/controlColor", POST)
        def controlColor(request: Request):
            data = request.json()
            r, g, b = data.get("r", 0), data.get("g", 0), data.get("b", 0)
            self.config['color']['r'] = int(r)
            self.config['color']['g'] = int(g)
            self.config['color']['b'] = int(b)
            self.write_config()
            self.display_time(self.hours_buffer, self.minutes_buffer)
            print(f"Set color saved: {r}, {g}, {b}")
            return Response(request, body='{"msg": "Color set"}')

        @self.server.route("/setTimeZone", POST)
        def setTimeZone(request: Request):
            data = request.json()
            
            tz = data.get("tz", 0)
            self.config['tz'] = float(tz)
            
            auto_dst = data.get("auto_dst", True)
            self.config['auto_dst'] = bool(auto_dst)
            
            self.write_config()
            self.adjust_time()
            print(f"Set tz saved: {tz}")
            return Response(request, body='{"msg": "Timezone set"}')

        @self.server.route("/setBrightness", POST)
        def setTimeZone(request: Request):
            data = request.json()
            
            auto_brightness = data.get("auto_brightness", True)
            self.config['auto_brightness'] = bool(auto_brightness)
            
            brightness = data.get("brightness", 100)
            self.config['brightness'] = int(brightness)/100.0
            
            self.write_config()
            self.set_brightness(self.config['brightness'])
            print(f"Set brightness: {brightness}")
            return Response(request, body='{"msg": "Brightness set"}')

        @self.server.route("/control/<action>", append_slash=True)
        def control(request: Request, action: str):
            if action == "light_on":
                self.is_light_allowed = True
                self.display_time(self.hours_buffer, self.minutes_buffer)
            elif action == "light_off":
                self.is_light_allowed = False
                self.disable_light()
            elif action == "disable_wifi":
                self.disable_wifi_now = True
            elif action == "tz_summer":
                self.config['tz'] = 2
                self.write_config()
                self.adjust_time()
            elif action == "tz_winter":
                self.config['tz'] = 1
                self.write_config()
                self.adjust_time()
            else:
                return Response(request, f"Unknown action ({action})")

            return Response(
                request, f"Action ({action}) performed"
            )