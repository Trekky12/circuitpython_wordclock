from WordClock import WordClock
import microcontroller
import time
import wifi

import traceback

def writeLog(message):
    try:
        with open("/logfile.txt", "a") as fp:
            if isinstance(message, Exception):
                traceback.print_exception(None, message, message.__traceback__, -1, fp)
            else:
                fp.write('{}\n'.format(message))
                fp.flush()
    except OSError as e:  # Typically when the filesystem isn't writeable...
        print("Error when writing file")
        pass

try:

    wordclock = WordClock()
    wordclock.begin()

    while True:
        wordclock.loop()

except Exception as e:
    #print(e)
    writeLog("There was an error")
    writeLog(e)
    time.sleep(10)
    microcontroller.reset()
