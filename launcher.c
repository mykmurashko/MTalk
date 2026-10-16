/* Native launcher for MTalk.app — built by install-app.sh.
 *
 * Runs Python *inside* this process instead of exec'ing a separate python
 * binary. That makes MTalk.app the real owner of the process, so macOS shows
 * its menu bar icon, attributes permissions (Accessibility, Input Monitoring,
 * Microphone) to MTalk, and runs it natively rather than under Rosetta.
 *
 * MTALK_DIR, MTALK_SITE, MTALK_CLAUDE and MTALK_PATH are passed in with -D.
 */
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    setenv("PYTHONPATH", MTALK_SITE, 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PATH", MTALK_PATH, 1);
    if (MTALK_CLAUDE[0]) setenv("MTALK_CLAUDE", MTALK_CLAUDE, 1);

    /* Python re-runs sys.executable for helpers (e.g. multiprocessing's
       resource tracker): pass those invocations straight through. */
    if (argc > 1) return Py_BytesMain(argc, argv);

    if (chdir(MTALK_DIR) != 0) { perror("chdir"); return 1; }

    char log[1024];
    snprintf(log, sizeof log, "%s/Library/Logs/MTalk.log", getenv("HOME"));
    freopen(log, "a", stdout);
    freopen(log, "a", stderr);

    char *args[] = {argv[0], "mtalk.py", NULL};
    return Py_BytesMain(2, args);
}
