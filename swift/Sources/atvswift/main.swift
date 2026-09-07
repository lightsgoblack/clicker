import Foundation

// atvswift: a native Swift proof that Apple TV pairing, verification, session encryption and
// commands work without pyatv. Port of the wire formats from pyatv (MIT).
//
//   atvswift verify --host H --port P --creds "ltpk:ltsk:atvid:clientid" [--press menu | --launch com.netflix.Netflix]
//   atvswift pair   --host H --port P [--name "Clicker"]      (asks for the PIN shown on the TV, prints credentials)

func arg(_ name: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: "--\(name)"), i + 1 < a.count else { return nil }
    return a[i + 1]
}
func fail(_ msg: String) -> Never { FileHandle.standardError.write(Data("error: \(msg)\n".utf8)); exit(1) }

let mode = CommandLine.arguments.dropFirst().first ?? "help"
guard let host = arg("host"), let portStr = arg("port"), let port = UInt16(portStr) else {
    print("usage: atvswift verify|pair --host <ip> --port <companion port> [--creds ...] [--press <button>] [--launch <bundle or url>] [--name <name>] [-v]")
    exit(mode == "help" ? 0 : 1)
}
let verbose = CommandLine.arguments.contains("-v")

setvbuf(stdout, nil, _IONBF, 0)
let sem = DispatchSemaphore(value: 0)
Task.detached {
    defer { sem.signal() }
    let c = Companion(host: host, port: port); c.verbose = verbose
    do {
        let t0 = Date()
        try await c.connect()
        print("connected to \(host):\(port)")
        switch mode {
        case "verify":
            guard let cs = arg("creds") else { fail("--creds required") }
            let creds = try HapCredentials(string: cs)
            try await c.verify(creds)
            print("pair-verify OK: encrypted session up (\(Int(Date().timeIntervalSince(t0) * 1000)) ms)")
            try await c.systemInfo(creds: creds, name: arg("name") ?? "atvswift")
            try await c.sessionStart()
            print("session started")
            if let b = arg("press") { try await c.press(b); print("pressed \(b)") }
            if let l = arg("launch") { try await c.launch(l); print("launched \(l)") }
        case "pair":
            try await c.pairStart()
            print("A PIN should be on the TV now. Type it and press Return: ", terminator: ""); fflush(stdout)
            guard let pin = readLine()?.trimmingCharacters(in: .whitespaces), !pin.isEmpty else { fail("no PIN") }
            let creds = try await c.pairFinish(pin: pin, name: arg("name") ?? "Clicker")
            print("paired! credentials (pyatv-compatible):\n\(creds.string)")
        default: fail("unknown mode \(mode)")
        }
    } catch { fail("\(error)") }
    c.close()
}
sem.wait()
