// swift-tools-version: 6.0

import PackageDescription

// The app code lives in the `CapDepMac` library target so it can be consumed
// both by the SwiftPM launcher executable (scripts/run-local-app.sh, CI
// `swift build`/`swift test`) and by the Xcode app shell in
// App/CapDepMac.xcodeproj, which links the `CapDepMacKit` library product.
let package = Package(
    name: "CapDepMac",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "CapDepMac", targets: ["CapDepMacLauncher"]),
        .library(name: "CapDepMacKit", targets: ["CapDepMac"]),
    ],
    targets: [
        .target(
            name: "CapDepMac",
            path: "Sources",
        ),
        .executableTarget(
            name: "CapDepMacLauncher",
            dependencies: ["CapDepMac"],
            path: "Launcher",
        ),
        .testTarget(
            name: "CapDepMacTests",
            dependencies: ["CapDepMac"],
            path: "Tests",
        ),
    ],
)
