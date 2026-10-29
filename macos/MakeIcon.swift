import AppKit

let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let outer = NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 214, yRadius: 214)
NSGradient(starting: NSColor(calibratedWhite: 0.22, alpha: 1), ending: NSColor(calibratedWhite: 0.055, alpha: 1))!.draw(in: outer, angle: -60)
NSColor(calibratedWhite: 1, alpha: 0.18).setStroke()
outer.lineWidth = 3
outer.stroke()
let center = NSBezierPath(roundedRect: NSRect(x: 208, y: 208, width: 608, height: 608), xRadius: 162, yRadius: 162)
NSGradient(starting: .white, ending: NSColor(calibratedWhite: 0.82, alpha: 1))!.draw(in: center, angle: -65)
let arrow = NSBezierPath()
arrow.move(to: NSPoint(x: 373, y: 373))
arrow.line(to: NSPoint(x: 650, y: 650))
arrow.move(to: NSPoint(x: 414, y: 650))
arrow.line(to: NSPoint(x: 650, y: 650))
arrow.line(to: NSPoint(x: 650, y: 414))
arrow.lineWidth = 51
arrow.lineCapStyle = .round
arrow.lineJoinStyle = .round
NSColor(calibratedWhite: 0.08, alpha: 1).setStroke()
arrow.stroke()
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
