// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "atvswift",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://github.com/attaswift/BigInt.git", from: "5.3.0"),
    ],
    targets: [
        .executableTarget(name: "atvswift", dependencies: ["BigInt"], path: "Sources/atvswift"),
    ]
)
