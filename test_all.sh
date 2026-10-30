set -e
clear
rm -rf build
rm -rf scratch/build
rm -rf tests/build
python -m unittest
hancho
hancho -f scratch/build.hancho
