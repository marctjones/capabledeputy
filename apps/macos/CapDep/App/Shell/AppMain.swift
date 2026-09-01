import CapDepMac

// Xcode app-shell entry point. The SwiftPM launcher target has its own
// equivalent (Launcher/main.swift); all real app code lives in the package.
@main
struct CapDepMacShell {
    static func main() {
        CapDepMacApp.main()
    }
}
