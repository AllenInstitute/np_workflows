import threading

from mvr import MVRConnector
from pprint import pprint

read_mvr = True


def mvr_thread(mvr):
    while read_mvr:
        for message in mvr.read():
            pprint(message)

def main():
    con = {'host': 'W10SV108131',
           'port': 50000}
    mvr = MVRConnector(con)

    t = threading.Thread(target=mvr_thread, args=(mvr,), daemon=True)
    t.start()

    global read_mvr

    while x := input('>>> '):
        if x == 's':
            mvr.take_snapshot()
        elif x == 'q':
            read_mvr = False
            t.join()
            break

if __name__ == '__main__':
    main()