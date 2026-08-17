import ctypes
import ctypes.util
import sys
import threading
import time

# ----------------------------------------------------------------------
# Define necessary GTK/GDK/WebKit callbacks and types
# ----------------------------------------------------------------------
# GCallback for "destroy" signal: void (*)(GtkWidget *, gpointer)
GtkDestroyCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

def _gtk_quit_cb(widget, user_data):
    """Callback that quits the GTK main loop."""
    # gtk_main_quit is a void(void) function; we can call it directly.
    gtk = ctypes.CDLL(ctypes.util.find_library('gtk-3'))
    if gtk:
        gtk.gtk_main_quit()

def launch_native_window(url: str):
    """
    Launch a native GTK+ window embedding a WebKit2 WebView using pure ctypes.
    Works on any Linux desktop with GTK+3 and WebKitGTK 4.0 installed.
    """
    # ---- Load system libraries ----
    lib_gtk = ctypes.CDLL(ctypes.util.find_library('gtk-3'))
    lib_gdk = ctypes.CDLL(ctypes.util.find_library('gdk-3'))
    lib_webkit = ctypes.CDLL(ctypes.util.find_library('webkit2gtk-4.0'))

    if not lib_gtk or not lib_gdk or not lib_webkit:
        print("❌ Missing required system libraries.")
        print("   Install with: sudo apt install libgtk-3-0 libwebkit2gtk-4.0-37")
        sys.exit(1)

    # ---- Set function signatures (restype & argtypes) for 64‑bit safety ----
    # GTK init
    lib_gtk.gtk_init.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.POINTER(ctypes.c_char))]
    lib_gtk.gtk_init.restype = None

    # Window creation
    lib_gtk.gtk_window_new.argtypes = [ctypes.c_int]        # GtkWindowType
    lib_gtk.gtk_window_new.restype = ctypes.c_void_p

    lib_gtk.gtk_window_set_title.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib_gtk.gtk_window_set_title.restype = None

    lib_gtk.gtk_window_set_default_size.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib_gtk.gtk_window_set_default_size.restype = None

    lib_gtk.gtk_window_set_position.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib_gtk.gtk_window_set_position.restype = None

    # Container & widget
    lib_gtk.gtk_container_add.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib_gtk.gtk_container_add.restype = None

    lib_gtk.gtk_widget_show_all.argtypes = [ctypes.c_void_p]
    lib_gtk.gtk_widget_show_all.restype = None

    # Main loop
    lib_gtk.gtk_main.argtypes = []
    lib_gtk.gtk_main.restype = None

    # Signal connection
    lib_gtk.g_signal_connect_data.argtypes = [
        ctypes.c_void_p,                     # instance
        ctypes.c_char_p,                     # detailed_signal
        ctypes.c_void_p,                     # c_handler (function pointer)
        ctypes.c_void_p,                     # data
        ctypes.c_void_p,                     # destroy_data
        ctypes.c_int                         # connect_flags
    ]
    lib_gtk.g_signal_connect_data.restype = ctypes.c_ulong

    # WebKit
    lib_webkit.webkit_web_view_new.argtypes = []
    lib_webkit.webkit_web_view_new.restype = ctypes.c_void_p

    lib_webkit.webkit_web_view_load_uri.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib_webkit.webkit_web_view_load_uri.restype = None

    # ---- Initialize GTK ----
    # Pass dummy argc/argv (GTK expects non-NULL argv[0], but we can pass NULLs)
    argc = ctypes.c_int(0)
    argv = ctypes.POINTER(ctypes.c_char)()
    lib_gtk.gtk_init(ctypes.byref(argc), ctypes.byref(argv))

    # ---- Create window ----
    window = lib_gtk.gtk_window_new(0)  # GTK_WINDOW_TOPLEVEL
    lib_gtk.gtk_window_set_title(window, b"Full-Perspective Interface")
    lib_gtk.gtk_window_set_default_size(window, 1280, 800)
    lib_gtk.gtk_window_set_position(window, 1)  # GTK_WIN_POS_CENTER

    # ---- Create WebView and load URL ----
    view = lib_webkit.webkit_web_view_new()
    lib_webkit.webkit_web_view_load_uri(view, url.encode('utf-8'))

    # ---- Assemble and show ----
    lib_gtk.gtk_container_add(window, view)
    lib_gtk.gtk_widget_show_all(window)

    # ---- Connect "destroy" to quit main loop ----
    destroy_cb = GtkDestroyCallback(_gtk_quit_cb)
    lib_gtk.g_signal_connect_data(
        window,
        b"destroy",
        ctypes.cast(destroy_cb, ctypes.c_void_p),
        None,
        None,
        0
    )

    # ---- Block until window closes ----
    print("🚀 Launching native GTK desktop window...")
    lib_gtk.gtk_main()
    print("GTK main loop exited.")
