import json
from typing import Union
from warnings import WarningMessage
import warnings
import requests

class MTrain:
    
    server = "http://mtrain:5000"
    get_script_endpoint = f"{server}/get_script/"
    handler = requests.get
    session = requests.session
    
    def __init__(self,mouse_id:Union[int,str]):
        self.mouse_id = str(mouse_id)
        
        
    @property
    def mouse_id(self):
        return self._mouse_id
    
    
    @mouse_id.setter
    def mouse_id(self,value: Union[int,str]):        
        response = requests.get(self.get_script_endpoint, data=json.dumps({"LabTracks_ID": str(value)}))
        if response.status_code == 200:
            self._mouse_id = str(value)
            
            
    @property
    def regimen(self) -> dict:
        """Returns dictionary containing 'id', 'name', 'stages', 'states'"""
        return requests.get(f"{self.server}/api/v1/regimens/{self.state['regimen_id']}").json()
    
    @property
    def script(self) -> dict:
        """Returns dict with strings for 'name' and 'stage', plus 'id' int"""
        return requests.get(f"{self.server}/get_script/", data=json.dumps({"LabTracks_ID": self.mouse_id})).json()["data"]
        
    @property
    def stage(self):
        for item in self.stages:
            if item['id'] == self.state['stage_id']:
                return item
    
    @property
    def state(self) -> dict:
        """Returns dict with values 'id', 'regimen_id', 'stage_id' - all ints"""
        return requests.get(f"{self.server}/api/v1/subjects/{self.mouse_id}").json()["state"]
        
    @state.setter
    def state(self, value):
        requests.put(
            f"{self.server}/set_state/{self.mouse_id}",
            data={
                # "username": "",
                # "password": "",
                "state": json.dumps(value),
            }
            )
        # with requests.session() as s:
        #     s.post(self.server,
        #         data={
        #     "username": "ben.hardcastle",
        #     "password": "",
        #     })
        #     s.post(f"{self.server}/set_state/{self.mouse_id}",
        #     data={
        #         "username": "",
        #         "password": "",
        #         "state": json.dumps(value),
        #     }
        #     )
        assert self.state == value, "set state failed"


    @property
    def states(self):
        return self.regimen['states']
    
    
    @property
    def stages(self):
        return self.regimen['stages']

        
    # @user_id.setter
    # def user_id(self,value):




    @classmethod
    def paginated_get(cls, route, page_size=10, offset=0):
        page_number = offset + 1  # page number is 1 based, offset is page
        result = requests.get(f"{route}?results_per_page={page_size}&page={page_number}").json()
        total_pages = result["total_pages"]
        if page_number == total_pages:
            new_offset = None
            has_more = False
        else:
            new_offset = offset + 1
            has_more = True
        
        return result["objects"], has_more, new_offset

    @classmethod
    def get_all_regimens(cls):
        # page_size = 200 is over the actual page size limit for the api but we don't appear to know what that value is
        #? 'total_pages': 18 
        all_regimens = []
        max_fetch=100
        page_size=200
        offset = 0
        for _ in range(max_fetch):
            regimens, has_more, new_offset = cls.paginated_get(f"{cls.server}/api/v1/regimens", page_size, offset)
            all_regimens.extend(regimens)
            if not has_more:
                break
            offset = new_offset
        else:
            warnings.WarningMessage("Failed to get full regimen list.")
        
        return all_regimens


    @classmethod
    def all_regimens(cls) -> dict:
        d = {}
        for val in cls.get_all_regimens():
            d.update({str(val['id']): val['name']})
        return d
    
    
    @classmethod
    def test(cls):
        response = requests.get(cls.server)
        return True if response.status_code == 200 else False
            

print(MTrain.test())
x = MTrain("366122")
x.state = x.regimen['states'][0]

illusion_regimens = {key:val for key,val in MTrain.all_regimens().items() if 'Illusion' in val}