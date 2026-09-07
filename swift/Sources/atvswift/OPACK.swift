import Foundation

/// OPACK: Apple's compact binary plist used by the Companion protocol.
/// Port of pyatv's support/opack.py. Values: nil, Bool, Int, Double, String, Data, [Any], [String: Any].
enum OPACK {
    static func pack(_ value: Any?) -> Data {
        var objs: [Data] = []
        return pack(value, &objs)
    }

    private static func pack(_ value: Any?, _ objs: inout [Data]) -> Data {
        var out = Data()
        switch value {
        case nil: out = Data([0x04])
        case let b as Bool: out = Data([b ? 1 : 2])
        case let i as Int:
            if i < 0x28 { out = Data([UInt8(i + 8)]) }
            else if i <= 0xFF { out = Data([0x30, UInt8(i)]) }
            else if i <= 0xFFFF { out = Data([0x31]) + le(UInt64(i), 2) }
            else if i <= 0xFFFF_FFFF { out = Data([0x32]) + le(UInt64(i), 4) }
            else { out = Data([0x33]) + le(UInt64(i), 8) }
        case let d as Double:
            var bits = d.bitPattern.littleEndian
            out = Data([0x36]) + Data(bytes: &bits, count: 8)
        case let s as String:
            let e = Data(s.utf8)
            if e.count <= 0x20 { out = Data([UInt8(0x40 + e.count)]) + e }
            else if e.count <= 0xFF { out = Data([0x61, UInt8(e.count)]) + e }
            else if e.count <= 0xFFFF { out = Data([0x62]) + le(UInt64(e.count), 2) + e }
            else { out = Data([0x63]) + le(UInt64(e.count), 3) + e }
        case let d as Data:
            if d.count <= 0x20 { out = Data([UInt8(0x70 + d.count)]) + d }
            else if d.count <= 0xFF { out = Data([0x91, UInt8(d.count)]) + d }
            else if d.count <= 0xFFFF { out = Data([0x92]) + le(UInt64(d.count), 2) + d }
            else { out = Data([0x93]) + le(UInt64(d.count), 4) + d }
        case let a as [Any?]:
            out = Data([UInt8(0xD0 + min(a.count, 0xF))])
            for x in a { out += pack(x, &objs) }
            if a.count >= 0xF { out += Data([0x03]) }
        case let dict as [String: Any?]:
            out = Data([UInt8(0xE0 + min(dict.count, 0xF))])
            for (k, v) in dict { out += pack(k, &objs); out += pack(v, &objs) }
            if dict.count >= 0xF { out += Data([0x03]) }
        default:
            fatalError("OPACK: unsupported type \(type(of: value))")
        }
        if let idx = objs.firstIndex(of: out) {
            if idx < 0x21 { return Data([UInt8(0xA0 + idx)]) }
            if idx <= 0xFF { return Data([0xC1, UInt8(idx)]) }
            return Data([0xC2]) + le(UInt64(idx), 2)
        } else if out.count > 1 {
            objs.append(out)
        }
        return out
    }

    static func unpack(_ data: Data) throws -> Any? {
        var objs: [Any?] = []
        var pos = data.startIndex
        return try unpack(data, &pos, &objs)
    }

    private static func unpack(_ d: Data, _ p: inout Int, _ objs: inout [Any?]) throws -> Any? {
        let t = d[p]; p += 1
        var value: Any? = nil
        var remember = true
        switch t {
        case 0x01: value = true; remember = false
        case 0x02: value = false; remember = false
        case 0x04: value = nil; remember = false
        case 0x05: value = d[p..<p+16]; p += 16
        case 0x06: value = Int(leRead(d, p, 8)); p += 8
        case 0x08...0x2F: value = Int(t) - 8; remember = false
        case 0x35: value = Double(Float(bitPattern: UInt32(leRead(d, p, 4)))); p += 4
        case 0x36: value = Double(bitPattern: leRead(d, p, 8)); p += 8
        case 0x30...0x33:
            let n = 1 << Int(t & 0xF); value = Int(leRead(d, p, n)); p += n
        case 0x40...0x60:
            let n = Int(t) - 0x40; value = String(decoding: d[p..<p+n], as: UTF8.self); p += n
        case 0x61...0x64:
            let nb = Int(t & 0xF); let n = Int(leRead(d, p, nb)); p += nb
            value = String(decoding: d[p..<p+n], as: UTF8.self); p += n
        case 0x70...0x90:
            let n = Int(t) - 0x70; value = Data(d[p..<p+n]); p += n
        case 0x91...0x94:
            let nb = 1 << (Int(t & 0xF) - 1); let n = Int(leRead(d, p, nb)); p += nb
            value = Data(d[p..<p+n]); p += n
        case 0xD0...0xDF:
            let count = Int(t & 0xF); var arr: [Any?] = []
            if count == 0xF { while d[p] != 0x03 { arr.append(try unpack(d, &p, &objs)) }; p += 1 }
            else { for _ in 0..<count { arr.append(try unpack(d, &p, &objs)) } }
            value = arr; remember = false
        case 0xE0...0xEF:
            let count = Int(t & 0xF); var dict: [String: Any?] = [:]
            if count == 0xF {
                while d[p] != 0x03 { let k = try unpack(d, &p, &objs); let v = try unpack(d, &p, &objs); dict[(k as? String) ?? "\(k ?? "nil")"] = v }
                p += 1
            } else {
                for _ in 0..<count { let k = try unpack(d, &p, &objs); let v = try unpack(d, &p, &objs); dict[(k as? String) ?? "\(k ?? "nil")"] = v }
            }
            value = dict; remember = false
        case 0xA0...0xC0:
            value = objs[Int(t) - 0xA0]; remember = false
        case 0xC1...0xC4:
            let n = Int(t) - 0xC0; let idx = Int(leRead(d, p, n)); p += n; value = objs[idx]; remember = false
        default:
            throw ATVError.proto("OPACK: unknown type byte 0x\(String(t, radix: 16))")
        }
        if remember { objs.append(value) }
        return value
    }

    private static func le(_ v: UInt64, _ n: Int) -> Data { Data((0..<n).map { UInt8((v >> (8 * UInt64($0))) & 0xFF) }) }
    private static func leRead(_ d: Data, _ p: Int, _ n: Int) -> UInt64 {
        var v: UInt64 = 0; for i in 0..<n { v |= UInt64(d[p + i]) << (8 * UInt64(i)) }; return v
    }
}

enum ATVError: Error, CustomStringConvertible {
    case proto(String), auth(String), network(String)
    var description: String {
        switch self { case .proto(let s), .auth(let s), .network(let s): return s }
    }
}
