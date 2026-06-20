// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "CapDepMac",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "CapDepMac", targets: ["CapDepMac"]),
    ],
    targets: [
        .executableTarget(
            name: "CapDepMac",
            path: "Sources",
        ),
    ],
)
