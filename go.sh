find . -name build | xargs rm -rf
clear
time hancho $@
time python3 -m unittest
