find . -name build | xargs rm -rf
clear
time hancho -v $@
time python3 -m unittest
