git checkout np
git pull origin np

robocopy .\np\ c:\progra~1\AIBS_MPE\workflow_launcher\np\ /MIR /E

set sourcefolder=.\np\workflows\
set destfolder=c:\ProgramData\AIBS_MPE\wfltk\workflows\

del %destfolder%\*.wfl

for /f %%d in ('dir %sourcefolder% /b /ad ') do (
    robocopy %sourcefolder%\%%d %destfolder% *.wfl /s /xf dynamic_routing.wfl
)
