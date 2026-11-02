// Isolates speech with Apple's AUSoundIsolation audio unit, offline and on the device.
// Usage: isolate --check
//        isolate <in.wav> <out.wav> <wet percent>
// Output has the input's format and exact length. The unit's latency is rendered past the end
// and dropped from the front, so timecodes never move.
import AVFoundation
import AudioToolbox
import Foundation

let chunk: AVAudioFrameCount = 4096
let parameterWetDry: AUParameterAddress = 0
let parameterSound: AUParameterAddress = 1
let highQualityVoice: AUValue = 0
let description = AudioComponentDescription(
    componentType: kAudioUnitType_Effect,
    componentSubType: 0x766F_6973,  // 'vois'. A literal, so this still compiles against an older SDK
    componentManufacturer: kAudioUnitManufacturer_Apple,
    componentFlags: 0,
    componentFlagsMask: 0
)

func say(_ record: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]),
       let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}

func fail(_ reason: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write("isolate: \(reason)\n".data(using: .utf8)!)
    exit(code)
}

func model(_ unit: AVAudioUnitEffect) -> String {
    guard #available(macOS 15, *), let sound = unit.auAudioUnit.parameterTree?.parameter(withAddress: parameterSound) else {
        return "standard"
    }
    sound.value = highQualityVoice
    return sound.value == highQualityVoice ? "high quality" : "standard"
}

func isolate(_ source: URL, _ dest: URL, wet: AUValue) throws -> [String: Any] {
    let input = try AVAudioFile(forReading: source)
    let format = input.processingFormat
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let unit = AVAudioUnitEffect(audioComponentDescription: description)
    engine.attach(player)
    engine.attach(unit)
    engine.connect(player, to: unit, format: format)
    engine.connect(unit, to: engine.mainMixerNode, format: format)
    try engine.enableManualRenderingMode(.offline, format: format, maximumFrameCount: chunk)
    let chosen = model(unit)
    unit.auAudioUnit.parameterTree?.parameter(withAddress: parameterWetDry)?.value = wet
    try engine.start()
    player.scheduleFile(input, at: nil)
    player.play()

    let total = AVAudioFramePosition(input.length)
    let latency = AVAudioFramePosition((unit.auAudioUnit.latency * format.sampleRate).rounded())
    let output = try AVAudioFile(forWriting: dest, settings: input.fileFormat.settings, commonFormat: format.commonFormat, interleaved: format.isInterleaved)
    guard let buffer = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: chunk) else {
        fail("no render buffer")
    }
    var skip = latency
    while output.length < total {
        let wanted = min(AVAudioFramePosition(chunk), total - output.length + skip)
        guard try engine.renderOffline(AVAudioFrameCount(wanted), to: buffer) == .success else {
            fail("the render stopped early")
        }
        if skip >= AVAudioFramePosition(buffer.frameLength) {
            skip -= AVAudioFramePosition(buffer.frameLength)
            continue
        }
        if skip > 0 {
            let left = Int(skip)
            let size = Int(buffer.frameLength) - left
            for channel in 0..<Int(format.channelCount) {
                guard let samples = buffer.floatChannelData?[channel] else { fail("no float samples") }
                samples.update(from: samples + left, count: size)
            }
            buffer.frameLength = AVAudioFrameCount(size)
            skip = 0
        }
        try output.write(from: buffer)
    }
    engine.stop()
    return ["frames": total, "latency": latency, "model": chosen]
}

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments == ["--check"] {
    guard AudioComponentFindNext(nil, [description]) != nil else { fail("this macOS has no AUSoundIsolation", code: 3) }
    say(["available": true, "model": model(AVAudioUnitEffect(audioComponentDescription: description))])
    exit(0)
}
guard arguments.count == 3, let wet = Float(arguments[2]) else {
    fail("usage: isolate --check | isolate <in.wav> <out.wav> <wet percent>", code: 2)
}
guard AudioComponentFindNext(nil, [description]) != nil else { fail("this macOS has no AUSoundIsolation", code: 3) }
do {
    say(try isolate(URL(fileURLWithPath: arguments[0]), URL(fileURLWithPath: arguments[1]), wet: wet))
} catch {
    fail("\(error)")
}
