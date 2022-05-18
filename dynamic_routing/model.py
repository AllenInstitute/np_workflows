class DynamicRouting:
    def __init__(self):
        """
        It may make more sense to store information in a class model instead of the global state variable.
        """
        self._user_name: str = ""
        self._mouse_id: str = ""
        self._experiment_id: str = ""

    @property
    def user_name(self):
        return self._user_name

    @user_name.setter
    def user_name(self, value):
        self._user_name = value

    @property
    def mouse_id(self):
        return  self._mouse_id

    @mouse_id.setter
    def mouse_id(self, value):
        self._mouse_id = value

    @property
    def experiment_id(self):
        return self._experiment_id

    @experiment_id.setter
    def experiment_id(self, value):
        self._experiment_id = value

    def platform_json(self):
