mkdir c:\progra~1\AIBS_MPE\workflow_launcher\dynamic_routing
robocopy .\dynamic_routing c:\progra~1\AIBS_MPE\workflow_launcher\dynamic_routing /MIR /E
mkdir c:\ProgramData\AIBS_MPE\wfltk\workflows
copy dynamic_routing\wfl_files\*.wfl c:\ProgramData\AIBS_MPE\wfltk\workflows\
