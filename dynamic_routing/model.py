import json
import time
from pathlib import Path


class DynamicRouting:
    def __init__(self):
        """
        It may make more sense to store information in a class model instead of the global state variable.
        """
        self._user_name: str = ""
        self._mouse_id: str = ""
        self._experiment_id: str = ""

    @property
    def user_name(self) -> str:
        return self._user_name

    @user_name.setter
    def user_name(self, value: str):
        self._user_name = value

    @property
    def mouse_id(self) -> str:
        return self._mouse_id

    @mouse_id.setter
    def mouse_id(self, value:str):
        self._mouse_id = value

    @property
    def experiment_id(self) -> str:
        return self._experiment_id

    @experiment_id.setter
    def experiment_id(self, value: str):
        self._experiment_id = value

    def write_platform_json(self, path: str):
        platform_data= dict(operator=self.user_name,
                             mouse_id=self.mouse_id,
                             experiment_id=self.experiment_id)

        filename = f"{self.experiment_id}_{self.mouse_id}_{time.strftime('%Y%m%d', time.localtime())}"

        with (Path(path)/filename).open('w') as platform_json:
            platform_json.write(json.dumps(platform_data))
