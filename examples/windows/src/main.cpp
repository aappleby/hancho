#include <windows.h>

LRESULT CALLBACK WindowProc(HWND hwnd, UINT uMsg, WPARAM wParam, LPARAM lParam) {
  switch (uMsg) {
    case WM_DESTROY: {
      PostQuitMessage(0);
      return 0;
    }
    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC hdc = BeginPaint(hwnd, &ps);
      FillRect(hdc, &ps.rcPaint, (HBRUSH)(COLOR_WINDOW + 1));
      DrawText(hdc, "Hello World", 11, &ps.rcPaint, 0);
      EndPaint(hwnd, &ps);
      return 0;
    }
  }
  return DefWindowProc(hwnd, uMsg, wParam, lParam);
}

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE, LPSTR /*pCmdLine*/, int nCmdShow) {
  WNDCLASS wc = { };

  wc.lpfnWndProc = WindowProc;
  wc.hInstance = hInstance;
  wc.lpszClassName = "Sample Window Class";

  RegisterClass(&wc);

  HWND hwnd = CreateWindowEx(0, "Sample Window Class", "Win32 Hello World",
    WS_OVERLAPPEDWINDOW, 400, 400, 400, 400,
    NULL, NULL, hInstance, NULL);

  ShowWindow(hwnd, nCmdShow);

  MSG msg = { };
  while (GetMessage(&msg, NULL, 0, 0)) {
    TranslateMessage(&msg);
    DispatchMessage(&msg);
  }

  return 0;
}
