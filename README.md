# services
![Services](./services.drawio.svg)

# requirements 

**workflow_launcher**

- download: http://aibspi2/release/workflow_toolkit/latest/

- unzip to `C:\Program Files\AIBS_MPE\workflow_launcher\` 

**router**

- download: http://aibspi2/release/router/latest/

- unzip to `C:\Program Files\AIBS_MPE\router\`

- `router.exe` must be started before `workflow_launcher.exe` (router can be left running if launcher restarted)

**graphviz**
    
- 64bit .exe: https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/3.0.0/windows_10_cmake_Release_graphviz-install-3.0.0-win64.exe

- homepage: https://graphviz.org/download/

- install and choose option to add to path

**workflows**

- install to workflow launcher dir
```
python -m pip install np_workflows -t C:\Progra~1\AIBS_MPE\workflow_launcher
```

launch router + workflow launcher with  `run.bat`

# notes

- if 'C:\ProgramData\AIBS_MPE\wfltk\workflows\' can't be reached, try running `workflow_launcher` as admin

- TextValue error displayed in WSE GUI may indicate an accidental capitalization, ie `type: Note` should be `type: note`