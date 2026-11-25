import Foundation
import ImageIO
import Vision

for path in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    // Do not force a language list: on some macOS installations a missing
    // Chinese Vision model makes the request fail with an opaque `nilError`.
    // Vision will auto-detect the installed OCR languages instead.
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
        let lines = (request.results ?? []).compactMap { observation in
            observation.topCandidates(1).first?.string
        }
        print("OCR_IMAGE\t\(path)")
        print(lines.joined(separator: "\n"))
    } catch {
        fputs("OCR_ERROR\t\(path)\t\(error)\n", stderr)
    }
}
