#!/usr/bin/env python3
"""SVG mit macOS-Bordmitteln als TIFF mit normaler und Retina-Auflösung rendern."""
import argparse
from pathlib import Path

import AppKit as A


def render(source: Path, output: Path) -> None:
    A.NSApplication.sharedApplication().setActivationPolicy_(A.NSApplicationActivationPolicyProhibited)
    image = A.NSImage.alloc().initWithContentsOfFile_(str(source.resolve()))
    if image is None:
        raise ValueError("DMG-Hintergrund konnte nicht geladen werden.")
    size = image.size()
    combined = A.NSImage.alloc().initWithSize_(size)
    for scale in (1, 2):
        bitmap = A.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, int(size.width * scale), int(size.height * scale), 8, 4, True, False,
            A.NSCalibratedRGBColorSpace, 0, 0)
        bitmap.setSize_(size)
        A.NSGraphicsContext.saveGraphicsState()
        try:
            A.NSGraphicsContext.setCurrentContext_(A.NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap))
            image.drawInRect_fromRect_operation_fraction_(A.NSMakeRect(0, 0, size.width, size.height),
                A.NSZeroRect, A.NSCompositingOperationCopy, 1)
        finally:
            A.NSGraphicsContext.restoreGraphicsState()
        combined.addRepresentation_(bitmap)
    data = combined.TIFFRepresentationUsingCompression_factor_(A.NSTIFFCompressionLZW, 1)
    if data is None or not data.writeToFile_atomically_(str(output), True):
        raise OSError("DMG-Hintergrund konnte nicht geschrieben werden.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(args.source, args.output)
