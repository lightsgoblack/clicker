import Foundation
import CryptoKit
import BigInt

// MARK: - Credentials (same 4-part hex string pyatv stores, so existing pairings work)

struct HapCredentials {
    let ltpk: Data     // Apple TV's long-term public key (Ed25519)
    let ltsk: Data     // our long-term secret key (Ed25519 seed)
    let atvId: Data    // Apple TV identifier
    let clientId: Data // our pairing id

    init(ltpk: Data, ltsk: Data, atvId: Data, clientId: Data) { self.ltpk = ltpk; self.ltsk = ltsk; self.atvId = atvId; self.clientId = clientId }
    init(string: String) throws {
        let parts = string.split(separator: ":").map(String.init)
        guard parts.count == 4, let a = Data(hex: parts[0]), let b = Data(hex: parts[1]), let c = Data(hex: parts[2]), let d = Data(hex: parts[3]) else { throw ATVError.auth("credentials must be 4 hex parts") }
        self.init(ltpk: a, ltsk: b, atvId: c, clientId: d)
    }
    var string: String { [ltpk, ltsk, atvId, clientId].map { $0.hex }.joined(separator: ":") }
}

// MARK: - Crypto helpers

func hkdf(_ salt: String, _ info: String, _ secret: Data) -> SymmetricKey {
    HKDF<SHA512>.deriveKey(inputKeyMaterial: SymmetricKey(data: secret), salt: Data(salt.utf8), info: Data(info.utf8), outputByteCount: 32)
}

/// ChaCha20-Poly1305 with HAP's 12-byte nonces (4 zero bytes + 8-byte label or counter).
struct Chacha {
    let outKey: SymmetricKey, inKey: SymmetricKey
    var outCounter: UInt64 = 0, inCounter: UInt64 = 0
    let nonceLength: Int  // 8 -> padded label/counter (pairing); 12 -> full little-endian counter (session)

    static func nonce8(_ label: String) -> Data { Data(repeating: 0, count: 4) + Data(label.utf8) }
    static func nonce8(counter: UInt64) -> Data { Data(repeating: 0, count: 4) + withUnsafeBytes(of: counter.littleEndian) { Data($0) } }
    static func nonce12(counter: UInt64) -> Data { withUnsafeBytes(of: counter.littleEndian) { Data($0) } + Data(repeating: 0, count: 4) }

    func encrypt(_ data: Data, nonce: Data, aad: Data? = nil) throws -> Data {
        let box = try ChaChaPoly.seal(data, using: outKey, nonce: ChaChaPoly.Nonce(data: nonce), authenticating: aad ?? Data())
        return box.ciphertext + box.tag
    }
    func decrypt(_ data: Data, nonce: Data, aad: Data? = nil) throws -> Data {
        let box = try ChaChaPoly.SealedBox(nonce: ChaChaPoly.Nonce(data: nonce), ciphertext: data.dropLast(16), tag: data.suffix(16))
        return try ChaChaPoly.open(box, using: inKey, authenticating: aad ?? Data())
    }
    mutating func encryptNext(_ data: Data, aad: Data) throws -> Data {
        let n = nonceLength == 12 ? Chacha.nonce12(counter: outCounter) : Chacha.nonce8(counter: outCounter); outCounter += 1
        return try encrypt(data, nonce: n, aad: aad)
    }
    mutating func decryptNext(_ data: Data, aad: Data) throws -> Data {
        let n = nonceLength == 12 ? Chacha.nonce12(counter: inCounter) : Chacha.nonce8(counter: inCounter); inCounter += 1
        return try decrypt(data, nonce: n, aad: aad)
    }
}

// MARK: - SRP-6a, HAP flavour (3072-bit group, g=5, SHA-512, username "Pair-Setup")

struct SRP {
    static let N = BigUInt("""
    FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF
    """.replacingOccurrences(of: "\n", with: ""), radix: 16)!
    static let g = BigUInt(5)
    static let user = "Pair-Setup"

    let a: BigUInt, A: BigUInt
    var K: Data = Data(), M1: Data = Data()

    init() {
        var seed = [UInt8](repeating: 0, count: 32); _ = SecRandomCopyBytes(kSecRandomDefault, 32, &seed)
        a = BigUInt(Data(seed)); A = SRP.g.power(a, modulus: SRP.N)
    }

    static func H(_ parts: Data...) -> Data { var h = SHA512(); for p in parts { h.update(data: p) }; return Data(h.finalize()) }
    static func pad(_ x: BigUInt) -> Data { let d = x.serialize(); return Data(repeating: 0, count: max(0, 384 - d.count)) + d }

    /// Process the server's salt and public key with the PIN; returns (A, M1).
    mutating func process(pin: String, salt: Data, B: Data) throws -> (Data, Data) {
        let Bn = BigUInt(B)
        guard Bn % SRP.N != 0 else { throw ATVError.auth("bad server public key") }
        let k = BigUInt(SRP.H(SRP.pad(SRP.N), SRP.pad(SRP.g)))
        let x = BigUInt(SRP.H(salt, SRP.H(Data("\(SRP.user):\(pin)".utf8))))
        let u = BigUInt(SRP.H(SRP.pad(A), SRP.pad(Bn)))
        // S = (B - k*g^x) ^ (a + u*x) mod N
        let gx = SRP.g.power(x, modulus: SRP.N)
        let base = (Bn + SRP.N * (k * gx / SRP.N + 1) - k * gx) % SRP.N
        let S = base.power(a + u * x, modulus: SRP.N)
        K = SRP.H(S.serialize())
        let hN = SRP.H(SRP.N.serialize()), hg = SRP.H(SRP.g.serialize())
        let hxor = Data(zip(hN, hg).map { $0 ^ $1 })
        M1 = SRP.H(hxor, SRP.H(Data(SRP.user.utf8)), salt, A.serialize(), Bn.serialize(), K)
        return (A.serialize(), M1)
    }

    func verify(serverProof: Data) -> Bool { SRP.H(A.serialize(), M1, K) == serverProof }
}

// MARK: - Data helpers

extension Data {
    init?(hex: String) {
        var d = Data(); var chars = hex[...]
        while chars.count >= 2 { guard let b = UInt8(chars.prefix(2), radix: 16) else { return nil }; d.append(b); chars = chars.dropFirst(2) }
        self = d
    }
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}
