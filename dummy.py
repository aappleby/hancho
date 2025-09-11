import sys

print(sys.modules[__name__])

def _foo():
    print("in foo")


class Dummy:
    def __init__(self):
        self.blep = 1
        self.__all__ = []

    def thinger(self):
        print(f"thinger {self}")

    #def __all__(self):
    #    return []

    foo = staticmethod(_foo)


sys.modules[__name__] = Dummy()

print(sys.modules[__name__])