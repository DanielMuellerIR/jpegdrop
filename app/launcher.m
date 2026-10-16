#import <Foundation/Foundation.h>
#include <Python.h>
#include <stdlib.h>

static void requirePythonStatus(PyStatus status) {
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }
}

int main(int argc, char **argv) {
    @autoreleasepool {
        NSBundle *bundle = NSBundle.mainBundle;
        NSString *resources = bundle.resourcePath;
        if (![bundle.bundleIdentifier isEqualToString:@"de.dm0.jpegdrop"] || resources == nil) {
            fprintf(stderr, "JPEGdrop: App-Bundle konnte nicht erkannt werden.\n");
            return 1;
        }
        NSString *pythonHome = [resources stringByAppendingPathComponent:@"python"];
        NSString *python = [pythonHome stringByAppendingPathComponent:@"bin/python3"];
        NSString *script = [resources stringByAppendingPathComponent:@"app/menu_app.py"];
        NSString *core = [resources stringByAppendingPathComponent:@"jpegdrop.py"];
        if (setenv("JPEGDROP_VENV_PY", python.fileSystemRepresentation, 1) != 0 ||
            setenv("JPEGDROP_SCRIPT", core.fileSystemRepresentation, 1) != 0 ||
            setenv("PYTHONDONTWRITEBYTECODE", "1", 1) != 0) {
            fprintf(stderr, "JPEGdrop: Laufzeitumgebung konnte nicht gesetzt werden.\n");
            return 1;
        }
        PyConfig config;
        PyConfig_InitIsolatedConfig(&config);
        config.write_bytecode = 0;
        config.parse_argv = 0;
        requirePythonStatus(PyConfig_SetBytesString(&config, &config.home, pythonHome.fileSystemRepresentation));
        requirePythonStatus(PyConfig_SetBytesString(&config, &config.program_name, argv[0]));
        requirePythonStatus(PyConfig_SetBytesString(&config, &config.run_filename, script.fileSystemRepresentation));
        requirePythonStatus(PyConfig_SetBytesArgv(&config, argc, argv));
        PyStatus status = Py_InitializeFromConfig(&config);
        PyConfig_Clear(&config);
        requirePythonStatus(status);
        return Py_RunMain();
    }
}
