set -e
clear
rm -rf build
rm -rf scratch/build
rm -rf tests/build
#python -m unittest
coverage erase
coverage run -a -m unittest
coverage run -a hancho.py -lverbose
echo
echo
coverage run -a hancho.py -f scratch/build.hancho --log.level=verbose
coverage html
