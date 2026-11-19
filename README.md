# AIVY Capstone

## One-click start on macOS

Double-click `Start AIVY.command`. It starts the FastAPI backend and Vite frontend,
waits for both services, and opens <http://127.0.0.1:5173/> in the default browser.
Keep the launcher window open while demonstrating AIVY.

Double-click `Stop AIVY.command` when the demonstration is finished. It only stops
processes created by the launcher.

The launcher expects the project's dependencies to have been installed once. The
Gemini key remains in `backend/.env`; it is not copied into either launcher.

If macOS refuses the first double-click, right-click the file, choose **Open**, then
confirm **Open** once.
