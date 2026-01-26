#!/usr/bin/env python3
"""
Process CSV file with GPS coordinates and convert to Lambert 72 format
Can either convert from GPS or extract existing Lambert 72 coordinates
"""

import csv
import sys
from pyproj import Transformer
import re

def parse_dms(dms_string):
    """Parse degrees/minutes/seconds string to decimal degrees"""
    pattern = r'(\d+)d(\d+)m([\d.]+)s'
    match = re.match(pattern, dms_string)
    
    if not match:
        raise ValueError(f"Invalid DMS format: {dms_string}")
    
    degrees = float(match.group(1))
    minutes = float(match.group(2))
    seconds = float(match.group(3))
    
    decimal_degrees = degrees + (minutes / 60.0) + (seconds / 3600.0)
    return decimal_degrees

def convert_from_gps(latitude_dms, longitude_dms, altitude):
    """Convert WGS84 GPS coordinates to Lambert 72"""
    lat_decimal = parse_dms(latitude_dms)
    lon_decimal = parse_dms(longitude_dms)
    
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:31370", always_xy=True)
    easting, northing = transformer.transform(lon_decimal, lat_decimal)
    
    return easting, northing, altitude

def process_csv_file(input_file, output_file, use_existing=True):
    """
    Process CSV file and output Lambert 72 coordinates
    
    Args:
        input_file: Path to input CSV file
        output_file: Path to output CSV file
        use_existing: If True, use existing Northing/Easting/Height columns;
                     If False, convert from GPS coordinates
    """
    with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
        # Skip header line
        header = infile.readline()
        
        for line in infile:
            fields = line.strip().split(',')
            
            if len(fields) < 15:
                continue
            
            name = fields[0]
            code = fields[1]
            
            if use_existing:
                # Use existing Lambert 72 coordinates from CSV
                # Column 12: Northing, Column 13: Easting, Column 14: Height
                try:
                    northing = float(fields[12])
                    easting = float(fields[13])
                    height = float(fields[14])
                except (ValueError, IndexError):
                    print(f"Warning: Could not parse existing coordinates for {name}")
                    continue
            else:
                # Convert from GPS coordinates
                # Column 8: Latitude, Column 9: Longitude, Column 10: Altitude
                try:
                    latitude_dms = fields[8]
                    longitude_dms = fields[9]
                    altitude = float(fields[10])
                    easting, northing, height = convert_from_gps(latitude_dms, longitude_dms, altitude)
                except (ValueError, IndexError) as e:
                    print(f"Warning: Could not convert GPS coordinates for {name}: {e}")
                    continue
            
            # Write output line
            output_line = f"{name},{easting:.3f},{northing:.3f},{height:.3f},{code}\n"
            outfile.write(output_line)
    
    print(f"Conversion complete! Output saved to: {output_file}")

def main():
    """Main function with command-line interface"""
    if len(sys.argv) < 3:
        print("Usage:")
        print("  python3 convert_csv.py <input_file> <output_file> [--convert-gps]")
        print("")
        print("Options:")
        print("  --convert-gps    Convert from GPS coordinates instead of using existing Lambert 72 values")
        print("")
        print("Default behavior: Extract existing Lambert 72 coordinates from CSV")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    use_existing = "--convert-gps" not in sys.argv
    
    if use_existing:
        print("Mode: Extracting existing Lambert 72 coordinates from CSV")
    else:
        print("Mode: Converting GPS coordinates to Lambert 72")
    
    process_csv_file(input_file, output_file, use_existing)

if __name__ == "__main__":
    main()
