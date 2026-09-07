import Foundation
import Network
import CryptoKit

/// The Companion protocol: 4-byte frame header (type + 24-bit length), OPACK bodies,
/// HAP pair-setup / pair-verify, then ChaCha20-Poly1305 on every frame.
final class Companion {
    enum Frame: UInt8 { case noop = 1, psStart = 3, psNext = 4, pvStart = 5, pvNext = 6, uOpack = 7, eOpack = 8, pOpack = 9 }

    let host: String, port: UInt16
    private let conn: NWConnection
    private var buffer = Data()
    private var cipher: Chacha? = nil
    private var xid = Int.random(in: 0..<65536)
    private var waiters: [String: CheckedContinuation<[String: Any?], Error>] = [:]
    private let queue = DispatchQueue(label: "companion")
    var verbose = false

    init(host: String, port: UInt16) {
        self.host = host; self.port = port
        conn = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!, using: .tcp)
    }

    func connect() async throws {
        final class Once: @unchecked Sendable { var done = false }
        let once = Once()
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            conn.stateUpdateHandler = { st in
                switch st {
                case .ready: if !once.done { once.done = true; c.resume() }
                case .failed(let e): if !once.done { once.done = true; c.resume(throwing: ATVError.network("connect failed: \(e)")) }
                case .cancelled: if !once.done { once.done = true; c.resume(throwing: ATVError.network("cancelled")) }
                default: break
                }
            }
            conn.start(queue: queue)
        }
        receiveLoop()
    }

    func close() { conn.cancel() }

    // MARK: frames

    private func send(_ type: Frame, _ body: Data) throws {
        var payload = body
        var length = body.count
        if cipher != nil && !body.isEmpty { length += 16 }
        let header = Data([type.rawValue, UInt8((length >> 16) & 0xFF), UInt8((length >> 8) & 0xFF), UInt8(length & 0xFF)])
        if cipher != nil && !body.isEmpty { payload = try cipher!.encryptNext(body, aad: header) }
        if verbose { print(">> \(type) \(body.count) bytes") }
        conn.send(content: header + payload, completion: .contentProcessed { _ in })
    }

    private func receiveLoop() {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            if let data { self.buffer += data; self.drain() }
            if error != nil || isComplete { self.failAll(ATVError.network("connection closed")); return }
            self.receiveLoop()
        }
    }

    private func drain() {
        while buffer.count >= 4 {
            let b = [UInt8](buffer.prefix(4))
            let len = Int(b[1]) << 16 | Int(b[2]) << 8 | Int(b[3])
            guard buffer.count >= 4 + len else { return }
            let header = Data(b); var payload = Data(buffer.dropFirst(4).prefix(len)); buffer = Data(buffer.dropFirst(4 + len))
            do {
                if cipher != nil && !payload.isEmpty { payload = try cipher!.decryptNext(payload, aad: Data(header)) }
                guard let type = Frame(rawValue: b[0]) else { continue }
                if verbose { print("<< \(type) \(payload.count) bytes") }
                if payload.isEmpty { continue }
                guard let dict = try OPACK.unpack(payload) as? [String: Any?] else { continue }
                let key: String
                switch type {
                case .psStart, .psNext: key = "PS"
                case .pvStart, .pvNext: key = "PV"
                default:
                    if let t = dict["_t"] as? Int, t == 1 { if verbose { print("   event \(dict["_i"] ?? "?")") }; continue }
                    key = "x\(dict["_x"] as? Int ?? -1)"
                }
                if let w = waiters.removeValue(forKey: key) { w.resume(returning: dict) }
            } catch { failAll(error) }
        }
    }

    private func failAll(_ e: Error) { for (_, w) in waiters { w.resume(throwing: e) }; waiters.removeAll() }

    private func exchange(_ type: Frame, _ dict: [String: Any?], key: String, timeout: Double = 8) async throws -> [String: Any?] {
        try await withCheckedThrowingContinuation { c in
            queue.async {
                self.waiters[key] = c
                do { try self.send(type, OPACK.pack(dict)) } catch { self.waiters.removeValue(forKey: key); c.resume(throwing: error); return }
                self.queue.asyncAfter(deadline: .now() + timeout) {
                    if let w = self.waiters.removeValue(forKey: key) { w.resume(throwing: ATVError.network("timeout waiting for \(key)")) }
                }
            }
        }
    }

    private func pairingData(_ resp: [String: Any?]) throws -> [UInt8: Data] {
        guard let pd = resp["_pd"] as? Data else { throw ATVError.auth("no pairing data in response") }
        let tlv = TLV.read(pd)
        if let e = tlv[TLV.error] { throw ATVError.auth("device returned pairing error \(e.hex) (wrong PIN, or pairing not allowed)") }
        return tlv
    }

    // MARK: pair-verify (existing credentials -> encrypted session)

    func verify(_ creds: HapCredentials) async throws {
        let eph = Curve25519.KeyAgreement.PrivateKey()
        let pub = eph.publicKey.rawRepresentation
        let m1 = try await exchange(.pvStart, ["_pd": TLV.write([(TLV.seqNo, Data([1])), (TLV.publicKey, pub)]), "_auTy": 4], key: "PV")
        let tlv = try pairingData(m1)
        guard let serverPub = tlv[TLV.publicKey], let encrypted = tlv[TLV.encryptedData] else { throw ATVError.auth("PV-M2 missing fields") }
        let shared = try eph.sharedSecretFromKeyAgreement(with: Curve25519.KeyAgreement.PublicKey(rawRepresentation: serverPub))
        let sharedData = shared.withUnsafeBytes { Data($0) }
        let sessionKey = hkdf("Pair-Verify-Encrypt-Salt", "Pair-Verify-Encrypt-Info", sharedData)
        let cc = Chacha(outKey: sessionKey, inKey: sessionKey, nonceLength: 8)
        let inner = TLV.read(try cc.decrypt(encrypted, nonce: Chacha.nonce8("PV-Msg02")))
        guard let ident = inner[TLV.identifier], let sig = inner[TLV.signature] else { throw ATVError.auth("PV-M2 inner TLV missing fields") }
        guard ident == creds.atvId else { throw ATVError.auth("device identity mismatch (\(ident.hex) vs \(creds.atvId.hex))") }
        let ltpk = try Curve25519.Signing.PublicKey(rawRepresentation: creds.ltpk)
        guard ltpk.isValidSignature(sig, for: serverPub + ident + pub) else { throw ATVError.auth("device signature invalid") }
        let ours = try Curve25519.Signing.PrivateKey(rawRepresentation: creds.ltsk)
        let ourSig = try ours.signature(for: pub + creds.clientId + serverPub)
        let m3 = try cc.encrypt(TLV.write([(TLV.identifier, creds.clientId), (TLV.signature, ourSig)]), nonce: Chacha.nonce8("PV-Msg03"))
        let m4 = try await exchange(.pvNext, ["_pd": TLV.write([(TLV.seqNo, Data([3])), (TLV.encryptedData, m3)])], key: "PV")
        _ = try pairingData(m4)
        let outKey = hkdf("", "ClientEncrypt-main", sharedData), inKey = hkdf("", "ServerEncrypt-main", sharedData)
        queue.sync { cipher = Chacha(outKey: outKey, inKey: inKey, nonceLength: 12) }
    }

    // MARK: pair-setup (PIN on the TV -> new credentials)

    private var setupSalt = Data(), setupB = Data()

    func pairStart() async throws {
        let m2 = try await exchange(.psStart, ["_pd": TLV.write([(TLV.method, Data([0])), (TLV.seqNo, Data([1]))]), "_pwTy": 1], key: "PS")
        let tlv = try pairingData(m2)
        guard let salt = tlv[TLV.salt], let B = tlv[TLV.publicKey] else { throw ATVError.auth("PS-M2 missing salt/public key") }
        setupSalt = salt; setupB = B
    }

    func pairFinish(pin: String, name: String) async throws -> HapCredentials {
        var srp = SRP()
        let (A, M1) = try srp.process(pin: pin, salt: setupSalt, B: setupB)
        let m4 = try await exchange(.psNext, ["_pd": TLV.write([(TLV.seqNo, Data([3])), (TLV.publicKey, A), (TLV.proof, M1)]), "_pwTy": 1], key: "PS")
        let tlv4 = try pairingData(m4)
        guard let proof = tlv4[TLV.proof], srp.verify(serverProof: proof) else { throw ATVError.auth("server proof mismatch (wrong PIN?)") }
        let signing = Curve25519.Signing.PrivateKey()
        let ltsk = signing.rawRepresentation, ltpk = signing.publicKey.rawRepresentation
        let clientId = Data(UUID().uuidString.utf8)
        let iosX = hkdf("Pair-Setup-Controller-Sign-Salt", "Pair-Setup-Controller-Sign-Info", srp.K).withUnsafeBytes { Data($0) }
        let sessionKey = hkdf("Pair-Setup-Encrypt-Salt", "Pair-Setup-Encrypt-Info", srp.K)
        let sig = try signing.signature(for: iosX + clientId + ltpk)
        let inner = TLV.write([(TLV.identifier, clientId), (TLV.publicKey, ltpk), (TLV.signature, sig), (TLV.name, OPACK.pack(["name": name]))])
        let cc = Chacha(outKey: sessionKey, inKey: sessionKey, nonceLength: 8)
        let enc = try cc.encrypt(inner, nonce: Chacha.nonce8("PS-Msg05"))
        let m6 = try await exchange(.psNext, ["_pd": TLV.write([(TLV.seqNo, Data([5])), (TLV.encryptedData, enc)]), "_pwTy": 1], key: "PS")
        let tlv6 = try pairingData(m6)
        guard let enc6 = tlv6[TLV.encryptedData] else { throw ATVError.auth("PS-M6 missing data") }
        let dev = TLV.read(try cc.decrypt(enc6, nonce: Chacha.nonce8("PS-Msg06")))
        guard let atvId = dev[TLV.identifier], let atvPub = dev[TLV.publicKey] else { throw ATVError.auth("PS-M6 inner TLV missing fields") }
        return HapCredentials(ltpk: atvPub, ltsk: ltsk, atvId: atvId, clientId: clientId)
    }

    // MARK: commands (after verify)

    @discardableResult
    func command(_ id: String, _ content: [String: Any?]) async throws -> [String: Any?] {
        let x = queue.sync { () -> Int in xid += 1; return xid }
        let resp = try await exchange(.eOpack, ["_i": id, "_t": 2, "_c": content, "_x": x], key: "x\(x)")
        if let em = resp["_em"] { throw ATVError.proto("\(id) failed: \(em ?? "?")") }
        return resp
    }

    func systemInfo(creds: HapCredentials, name: String) async throws {
        try await command("_systemInfo", ["_bf": 0, "_cf": 512, "_clFl": 128, "_i": "atvswift", "_idsID": creds.clientId,
                                          "_pubID": "AA:BB:CC:DD:EE:FF", "_sf": 256, "_sv": "170.18", "model": "iPhone14,3", "name": name])
    }

    func sessionStart() async throws {
        let sid = Int.random(in: 0 ..< Int(UInt32.max))
        try await command("_sessionStart", ["_srvT": "com.apple.tvremoteservices", "_sid": sid])
    }

    static let hid: [String: Int] = ["up": 1, "down": 2, "left": 3, "right": 4, "menu": 5, "select": 6, "home": 7, "volume_up": 8, "volume_down": 9,
                                     "siri": 10, "screensaver": 11, "sleep": 12, "wake": 13, "play_pause": 14]

    func press(_ button: String) async throws {
        guard let code = Companion.hid[button] else { throw ATVError.proto("unknown button \(button)") }
        try await command("_hidC", ["_hBtS": 1, "_hidC": code])
        try await command("_hidC", ["_hBtS": 2, "_hidC": code])
    }

    func launch(_ target: String) async throws {
        let key = target.contains("://") ? "_urlS" : "_bundleID"
        try await command("_launchApp", [key: target])
    }
}
