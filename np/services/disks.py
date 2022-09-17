import shutil
try:
    import np.services.config as config
except ImportError:
    import config

ALL_COMPS: dict[str,str] = config.ConfigHTTP.get_np_computers([0,1])
DIVIDER_LENGTH = 40

def comp_from_hostname(hostname: str) -> str:
    for comp, host in ALL_COMPS.items():
        if host == hostname:
            return comp
    return ""

def rig_from_comp(comp: str) -> str:
    return comp.split(".")[0]

class Drive:
    def __init__(self, letter:str, hostname:str):
        self.letter = letter
        self.hostname = hostname
        self.comp = comp_from_hostname(hostname)
        self.rig = rig_from_comp(self.comp)
        
    @property
    def usage(self):
        try:
            return shutil.disk_usage(f"//{self.hostname}/{self.letter}$")
        except PermissionError:
            return None
    
    def __print__(self):
        print(f"{self.letter}:/")
        usage = self.usage
        if not usage:
            return '- not available -'
        length = DIVIDER_LENGTH - 10
        used = '#'
        free = '-'
        fraction = usage.used / usage.total
        print(f"[{used*round(fraction*length)}{free*round((1-fraction)*length)}] {usage.used/1e9:.2f} GB / {usage.total/1e9:.2f} GB")


if __name__ == "__main__":
    
    first_comp = "Acq"
    
    for comp,hostname in ALL_COMPS.items():
        
        if first_comp in comp:
            print(f"\n{comp.split('-')[0]}\n{'='*DIVIDER_LENGTH}")
            
        print(f"\n{comp.split('-')[1]}")
        
        Drive("C", hostname).__print__()
        if 'acq' in comp.lower():
            Drive("A", hostname).__print__()
            Drive("B", hostname).__print__()
        