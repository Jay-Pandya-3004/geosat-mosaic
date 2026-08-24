"""Canonical per-satellite metadata: footprint geometry, operational and
data-availability windows, instrument/channel definitions. Sourced from
WMO OSCAR (https://space.oscar.wmo.int) cross-referenced against each
agency's actual data archive coverage -- see each entry's Metadata_Sources.

Migrated from the original standalone MOSAIC prototype; extended with
satellites needed for the 6-slot global mosaic (see docs/README.md).
"""

import math

import numpy as np

def geo_footprint_polygon(subsat_lat, subsat_lon,
                          earth_radius=6378.137,
                          orbit_radius=42164.363,
                          n_points=360):
    """Compute a geostationary satellite's visible-disk footprint on a spherical Earth.

    Traces the limb of the Earth-disk visible from a geostationary
    satellite at (`subsat_lat`, `subsat_lon`), using a spherical-Earth
    approximation (see :func:`geostationary_footprint_wgs84` for the
    WGS84-ellipsoid version).

    Args:
        subsat_lat: Sub-satellite latitude, in degrees.
        subsat_lon: Sub-satellite longitude, in degrees.
        earth_radius: Earth radius, in km.
        orbit_radius: Geostationary orbit radius (from Earth's center),
            in km.
        n_points: Number of azimuth samples around the footprint.

    Returns:
        List of `(lat, lon)` tuples, in degrees, tracing the footprint
        polygon.
    """
    # convert to radians
    lat0 = np.radians(subsat_lat)
    lon0 = np.radians(subsat_lon)

    # viewing angle
    theta = np.arccos(earth_radius / orbit_radius)

    footprint = []

    for az in np.linspace(0, 2*np.pi, n_points):

        lat = np.arcsin(
            np.sin(lat0) * np.cos(theta) +
            np.cos(lat0) * np.sin(theta) * np.cos(az)
        )

        lon = lon0 + np.arctan2(
            np.sin(az) * np.sin(theta) * np.cos(lat0),
            np.cos(theta) - np.sin(lat0) * np.sin(lat)
        )

        footprint.append((np.degrees(lat), np.degrees(lon)))

    return footprint

# WGS84 constants
a = 6378.137          # equatorial radius
b = 6356.7523142      # polar radius

def geostationary_footprint_wgs84(subsat_lon_deg,
                                  sat_alt_km=35786.226,
                                  n_points=360):
    """Compute a geostationary satellite's visible-disk footprint on the WGS84 ellipsoid.

    Traces the limb of the Earth-disk visible from a geostationary
    satellite at `subsat_lon_deg` (assumed equatorial), by intersecting
    view rays with the WGS84 ellipsoid rather than a sphere -- more
    accurate than :func:`geo_footprint_polygon` for the true (slightly
    oblate) Earth shape.

    Args:
        subsat_lon_deg: Sub-satellite longitude, in degrees.
        sat_alt_km: Satellite altitude above the equatorial radius, in km.
        n_points: Number of azimuth samples around the footprint.

    Returns:
        List of `(lat, lon)` tuples, in degrees, tracing the footprint
        polygon. Azimuths whose view ray doesn't intersect the ellipsoid
        (discriminant < 0) are silently skipped, so the result may have
        fewer than `n_points` entries.
    """
    # satellite position in ECEF
    lon = np.radians(subsat_lon_deg)
    rs = a + sat_alt_km

    sat = np.array([
        rs * np.cos(lon),
        rs * np.sin(lon),
        0.0
    ])

    footprint = []

    for az in np.linspace(0, 2*np.pi, n_points):

        # viewing direction
        d = np.array([
            -np.cos(az)*np.cos(lon),
            -np.cos(az)*np.sin(lon),
            np.sin(az)
        ])

        # solve quadratic for ray–ellipsoid intersection
        A = (d[0]**2 + d[1]**2)/a**2 + (d[2]**2)/b**2
        B = 2*((sat[0]*d[0] + sat[1]*d[1])/a**2 + (sat[2]*d[2])/b**2)
        C = (sat[0]**2 + sat[1]**2)/a**2 + (sat[2]**2)/b**2 - 1

        disc = B**2 - 4*A*C

        if disc >= 0:
            t = (-B - np.sqrt(disc)) / (2*A)
            p = sat + t*d

            x,y,z = p

            lon_p = np.degrees(np.arctan2(y,x))
            lat_p = np.degrees(np.arctan2(z,
                     np.sqrt(x**2+y**2)))

            footprint.append((lat_p,lon_p))

    return footprint

def geo_satellite_bbox(sat_lat, sat_lon, orbit_radius=42164.363, earth_radius=6378.137):
    """Compute an axis-aligned lat/lon bounding box for a geostationary satellite's view.

    A cheap rectangular approximation of the visible disk (unlike the
    polygon footprint functions above), useful for quick coverage/
    overlap checks.

    Args:
        sat_lat: Sub-satellite latitude, in degrees.
        sat_lon: Sub-satellite longitude, in degrees.
        orbit_radius: Geostationary orbit radius (from Earth's center),
            in km.
        earth_radius: Earth radius, in km.

    Returns:
        Dict with keys ``lon_min``, ``lat_max``, ``lat_min``, ``lon_max``
        (degrees).
    """
    # maximum viewing angle
    theta = math.degrees(math.acos(earth_radius / orbit_radius))

    # max visible latitude
    lat_max = math.degrees(math.atan(
        earth_radius * math.sin(math.radians(theta)) /
        (orbit_radius - earth_radius * math.cos(math.radians(theta)))
    ))

    lat_min = -lat_max

    lon_min = sat_lon - theta
    lon_max = sat_lon + theta

    return {
        "lon_min": lon_min,
        "lat_max": lat_max,
        "lat_min": lat_min,
        "lon_max": lon_max
    }

"""
Per-satellite dict schema (see any Satellites_Objects[...] entry below):

Satellite
Satellite_Full_Name
Satellite_Status
Sat_Lat-On_Operation_Start
Sat_Lon-On_Operation_Start
Sat_Lat_Lon_Change_During_Active_Operation
Operation_Start_Date
Operation_End_Date
Data_Available
Data_Available_Start_Date
Date_Available_End_Date
Data_Dissemination_From
Filename_Example
File_Type
File_Path_Example
File_Path_RegEx
Channel_Band_Variable_Name
File_Name_Regex
Imager-Instrument
Channel_no
Channel_Name
Channel_Type
Central_Wavelength_Nm
Spectral_Interval_Nm
SNR_OR_NEΔT_AT_Specified_Input
Spatial_Resolution_Km
Full_Disk-Temporal_Resolution
Metadata-Sources

Scan_Timing (per-instrument dict, added 2026-07-30, backed by
per-satellite research behind these fields): the mosaic pipeline needs each file's scan
MID-time (midpoint of scan start/end, not start/end/completion) to compare
sync across satellites. Fidelity for this differs sharply by satellite --
this dict records what's actually achievable, not a false-precision single
number:
  - Full_Disk_Repeat_Cadence_Minutes: time between successive full-disk
    product starts (the nominal "refresh rate").
  - Full_Disk_Scan_Duration_Minutes: how long the physical scan itself
    takes within that cadence slot (imaging only, excludes calibration/
    retrace/idle) -- None if not documented by any primary source.
  - Mid_Time_Source: one of "filename" (mid-time is directly computable
    from the filename/S3-key alone), "file_header" (must open the file and
    read a header/attribute field -- filename alone is insufficient or
    unverified), or "approximation" (no exact source exists; a documented
    but unconfirmed estimate is the best available).
  - Mid_Time_Method: short description of the exact field/formula to use.
  - Mid_Time_Confidence: "exact" (directly sourced, e.g. a precomputed
    mid-time variable or true start+end fields), "approximate" (a stated
    but inferred/derived formula, e.g. filename_time + fixed offset), or
    "unverified" (no primary source confirms the relationship at all).
"""

Satellites_Objects = {}

Satellites = ["goes-08", 
              "goes-09", 
              "goes-10", 
              "goes-11", 
              "goes-12", 
              "goes-13", 
              "goes-14",
              "goes-15", 
              "goes-16", 
              "goes-17", 
              "goes-18", 
              "goes-19", 
              "meteosat-05", 
              "meteosat-06", 
              "meteosat-07", 
              "meteosat-08", 
              "meteosat-09", 
              "meteosat-10", 
              "meteosat-11", 
              "himawari-08",
              "himawari-09",
              "himawari-10",
              "mtsat-01",
              "mtsat-02",
              "gk2a",
              "gk2b",
              "kalpana-1",
              "insat-3d",
              "insat-3dr",
              "insat-3ds"]

Satellites_Objects["goes-08"] ={
    "Satellite_ID": "GOES-08",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES I–M",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 8",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -75.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -75.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -180.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": 5.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": -156.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 6.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "1994-04-13T00:00:00",
    "Operation_End_Date": "2004-05-05T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "1994-09-01T00:00:00",
    "Date_Available_End_Date": "2003-04-01T17:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes08.1994.09.01.0000.v01.nc	",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/1994/09/GridSat-GOES.goes08.1994.09.01.0000.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes08.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes08.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C00681#:~:text=The%20geosynchronous%20plane%20is%20about,at%20135%20degrees%20West%20longitude.",
                         "https://space.oscar.wmo.int/satellites/view/goes_8",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_8_11"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.75,
                    "Spectral_Interval_Um": 0.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.95,
                    "Spectral_Interval_Um": 0.9,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-09"] = {
    "Satellite_ID": "GOES-09",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES I–M",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 9",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -135.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -135.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": 150.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": -55.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": 143.7,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": -53.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "1995-05-23T00:00:00",
    "Operation_End_Date": "2006-07-24T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "1995-09-01T00:00:00",
    "Date_Available_End_Date": "2004-07-13T11:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes09.1995.09.01.0000.v01.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/1995/09/GridSat-GOES.goes09.1995.09.01.0000.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes09.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes09.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C00681#:~:text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59&text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59",
                         "https://space.oscar.wmo.int/satellites/view/goes_9",
                         "https://space.oscar.wmo.int/satellites/view/goes_9_gms_backup",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_8_11"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.75,
                    "Spectral_Interval_Um": 0.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.95,
                    "Spectral_Interval_Um": 0.9,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-10"] = {
    "Satellite_ID": "GOES-10",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES I–M",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 10",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -135.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -135.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": 150.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": -55.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": 143.7,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": -53.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "1997-04-25T00:00:00",
    "Operation_End_Date": "2009-12-02T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "1998-07-21T16:00:00",
    "Date_Available_End_Date": "2007-12-17T18:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes10.1998.07.21.1600.v01.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/1998/07/GridSat-GOES.goes10.1998.07.21.1600.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes10.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes10.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C00681#:~:text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59&text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59",
                         "https://space.oscar.wmo.int/satellites/view/goes_10",
                         "https://space.oscar.wmo.int/satellites/view/goes_10_s_america",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_8_11"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.75,
                    "Spectral_Interval_Um": 0.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.95,
                    "Spectral_Interval_Um": 0.9,
                    "SNR_OR_NEΔT_AT_Specified_INPUT": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-11"] = {
    "Satellite_ID": "GOES-11",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES I–M",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 11",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -135.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -135.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": 150.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": -55.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": 143.7,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": -53.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2000-05-03T00:00:00",
    "Operation_End_Date": "2011-12-05T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2006-06-21T16:00:00",
    "Date_Available_End_Date": "2010-12-31T23:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes11.2006.06.21.1600.v01.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/2006/06/GridSat-GOES.goes11.2006.06.21.1600.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes11.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes11.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C00681#:~:text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59&text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59",
                         "https://space.oscar.wmo.int/satellites/view/goes_11",
                         "https://space.oscar.wmo.int/satellites/view/goes_11_s_america",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_8_11"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.75,
                    "Spectral_Interval_Um": 0.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.95,
                    "Spectral_Interval_Um": 0.9,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-12"] = {
    "Satellite_ID": "GOES-12",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES I–M",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 12",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -75.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -75.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -180.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": 5.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": -156.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 6.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2001-07-23T00:00:00",
    "Operation_End_Date": "2010-05-10T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2003-04-01T18:00:00",
    "Date_Available_End_Date": "2010-06-17T22:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes12.2003.04.01.1800.v01.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/2003/04/GridSat-GOES.goes12.2003.04.01.1800.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes12.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes12.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C00681#:~:text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59&text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59",
                         "https://space.oscar.wmo.int/satellites/view/goes_12",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_12_15"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.55,
                    "Spectral_Interval_Um": 1.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.35,
                    "Spectral_Interval_Um": 0.7,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-13"] = {
    "Satellite_ID": "GOES-13",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES N–P",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 13",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -105.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -105.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -188.0,
        "lat_max": 10.0,
        "lat_min": -10.0,
        "lon_max": -25.0
    },
    "Footprint_Bounding_Box": {
        'lon_min': -186.3, 
        'lat_max': 8.7, 
        'lat_min': -8.7, 
        'lon_max': -23.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2006-05-24T00:00:00",
    "Operation_End_Date": "2019-10-01T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2008-12-15T05:00:00",
    "Date_Available_End_Date": "2017-12-31T23:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes13.2008.12.15.0500.v01.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/2008/12/GridSat-GOES.goes13.2008.12.15.0500.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes13.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes13.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C00681#:~:text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59&text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59",
                         "https://space.oscar.wmo.int/satellites/view/goes_13",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_12_15"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.55,
                    "Spectral_Interval_Um": 1.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.35,
                    "Spectral_Interval_Um": 0.7,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-14"] = {
    "Satellite_ID": "GOES-14",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES N–P",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 14",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -105.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -105.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -188.0,
        "lat_max": 10.0,
        "lat_min": -10.0,
        "lon_max": -25.0
    },
    "Footprint_Bounding_Box": {
        'lon_min': -186.3, 
        'lat_max': 8.7, 
        'lat_min': -8.7, 
        'lon_max': -23.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2009-06-27T00:00:00",
    "Operation_End_Date": "2020-03-02T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2011-08-10T17:00:00",
    "Date_Available_End_Date": "2016-09-27T14:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes14.2011.08.10.1700.v01.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/2011/08/GridSat-GOES.goes14.2011.08.10.1700.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes14.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes14.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C00681#:~:text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59&text=Last%20Modified%3A%202025%2D07%2D15T21%3A26%3A59",
                         "https://www.swpc.noaa.gov/news/noaanesdis-has-extended-operational-period-both-goes-1415-02-march-2020",
                         "https://space.oscar.wmo.int/satellites/view/goes_14",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_12_15"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.55,
                    "Spectral_Interval_Um": 1.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.35,
                    "Spectral_Interval_Um": 0.7,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-15"] = {
    "Satellite_ID": "GOES-15",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES N–P",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 15",
    "Satellite_Status": "Decommissioned",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -135.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -135.0
    },
    "Satellite_Height_Variable_Name": "satrad",
    "Satellite_Nominal_Lat_Variable_Name": "satlat",
    "Satellite_Nominal_Lon_Variable_Name": "satlon",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": 150.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": -55.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": 143.7,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": -53.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2010-03-04T00:00:00",
    "Operation_End_Date": "2020-03-02T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2011-12-06T16:00:00",
    "Date_Available_End_Date": "2017-11-29T23:00:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "GridSat-GOES.goes15.2011.12.06.1600.v01.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/2011/12/GridSat-GOES.goes15.2011.12.06.1600.v01.nc",
    "File_Path_RegEx": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/\\d{4}/\\d{2}/GridSat-GOES.goes15.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "File_Name_Regex": "GridSat-GOES.goes15.\\d{4}.\\d{2}.\\d{2}.\\d{4}.v01.nc",
    "Metadata_Sources": ["https://www.swpc.noaa.gov/news/noaanesdis-has-extended-operational-period-both-goes-1415-02-march-2020",
                         "https://space.oscar.wmo.int/satellites/view/goes_15",
                         "https://space.oscar.wmo.int/instruments/view/imager_goes_12_15"
                        ],
    "Instruments": {
        "Imager": {
            "Imager_Instrument": "GOES Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Visible",
                    "Channel_Band_Variable_Name": "ch1",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.65,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "250 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Shortwave Infrared",
                    "Channel_Band_Variable_Name": "ch2",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.2,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.11 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Water Vapor",
                    "Channel_Band_Variable_Name": "ch3",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.55,
                    "Spectral_Interval_Um": 1.5,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.14 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Longwave Infrared",
                    "Channel_Band_Variable_Name": "ch4",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.70,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.09 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Cloud and Surface Temperature",
                    "Channel_Band_Variable_Name": "ch5",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.35,
                    "Spectral_Interval_Um": 0.7,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.70 K @ 300 K",
                    "Spatial_Resolution_Km": 4.0
                }
            }
        }
    }
}

Satellites_Objects["goes-16"] = {
    "Satellite_ID": "GOES-16",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES R–T",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 16",
    "Satellite_Status": "Standby",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -75.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -75.0
    },
    "Satellite_Height_Variable_Name": "nominal_satellite_height",
    "Satellite_Nominal_Lat_Variable_Name": "nominal_satellite_subpoint_lat",
    "Satellite_Nominal_Lon_Variable_Name": "nominal_satellite_subpoint_lon",
    "Satellite_Time_Variable_Name": "t",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -180.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": 5.0
    },
    "Footprint_Bounding_Box": {
        'lon_min': -156.3, 
        'lat_max': 66.5, 
        'lat_min': -66.5, 
        'lon_max': 6.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Longitude_History_Note": "GOES-16 served as the operational GOES-East satellite (-75.0) from 2017-12-18 until 2025-04-07, when GOES-19 took over. It is now an on-orbit Standby spare at 104.7 W (per NOAA Beginner's Guide to GOES-R Series Data, Sept 2025, p.5), not deactivated hardware -- Nominal_Longitude_Deg below reflects its primary operational slot, not its current standby position.",
    "Operation_Start_Date": "2016-11-19T00:00:00",
    "Operation_End_Date": "2025-04-07T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2017-02-28T23:16:00",
    "Date_Available_End_Date": "2025-02-19T23:59:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "OR_ABI-L1b-RadF-M3C01_G16_s20170592306087_e20170592316454_c20170592316498.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://noaa-goes16.s3.amazonaws.com/ABI-L1b-RadF/2017/059/23/OR_ABI-L1b-RadF-M3C01_G16_s20170592306087_e20170592316454_c20170592316498.nc",
    "File_Path_RegEx": "https://noaa-goes16.s3.amazonaws.com/ABI-L1b-RadF/\\d{4}/\\d{3}/\\d{2}/OR_ABI-L1b-RadF-M3C\\d{2}_G16_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "File_Name_Regex": "OR_ABI-L1b-RadF-M3C\\d{2}_G16_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/goes_16",
                         "https://space.oscar.wmo.int/instruments/view/abi"
                        ],
    "Instruments": {
        "ABI": {
            "ABI_Instrument": "Advanced Baseline Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 16,
            "Full_Disk_Temporal_Resolution_Minutes": 15,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 15,
                "Full_Disk_Scan_Duration_Minutes": None,
                "Mid_Time_Source": "filename",
                "Mid_Time_Method": "netCDF `t` variable is a precomputed J2000 scan mid-time; equivalently (s+e)/2 from the 14-digit sYYYYDDDHHMMSSs/eYYYYDDDHHMMSSs filename fields -- both are the true scan start/end (not product-completion time), per the noaa-goes16 AWS README.",
                "Mid_Time_Confidence": "exact",
            },
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Band 1 - Blue",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.47,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Band 2 - Red",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.64,
                    "Spectral_Interval_Um": 0.1,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 0.5,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Band 3 - Near-Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 0.86,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Band 4 - Cirrus",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.38,
                    "Spectral_Interval_Um": 0.03,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Band 5 - Snow/Ice",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.61,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    # NOAA's own official ABI bands table (goes-r.gov/spacesegment/
                    # ABI-tech-summary.html) gives Band 5 (1.6um Snow/Ice) a
                    # native resolution of 1km, same as Bands 1 and 3 -- only
                    # Band 2 is finer (0.5km) and Band 4 plus Bands 6-16 are
                    # the coarser 2km group. This band is this project's own
                    # Phase-2 SWIR channel (see band_registry.py), so this
                    # value feeds downsample/selection/metadata-disclosure
                    # decisions for every GOES SWIR pixel -- it was previously
                    # listed here as 2.0km, which was wrong.
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                6: {
                    "Channel_no": 6,
                    "Channel_Name": "Band 6 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 2.26,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                7: {
                    "Channel_no": 7,
                    "Channel_Name": "Band 7 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                8: {
                    "Channel_no": 8,
                    "Channel_Name": "Band 8 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.15,
                    "Spectral_Interval_Um": 0.09,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                9: {
                    "Channel_no": 9,
                    "Channel_Name": "Band 9 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    # NOAA's own official ABI bands table (goes-r.gov/
                    # spacesegment/ABI-tech-summary.html) gives Band 9
                    # ("Midlevel water vapor") a central wavelength of
                    # 6.9um, matching what this file already stores for
                    # Himawari's equivalent WV channel (B09, also 6.9um).
                    # Previously listed here as 7.0um, which was off.
                    "Central_Wavelength_Um": 6.9,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                10: {
                    "Channel_no": 10,
                    "Channel_Name": "Band 10 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 7.4,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                11: {
                    "Channel_no": 11,
                    "Channel_Name": "Band 11 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 8.5,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_INPUT": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                12: {
                    "Channel_no": 12,
                    "Channel_Name": "Band 12 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 9.7,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                13: {
                    "Channel_no": 13,
                    "Channel_Name": "Band 13 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.3,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                14: {
                    "Channel_no": 14,
                    "Channel_Name": "Band 14 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.2,
                    "Spectral_Interval_Um": 0.08,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                15: {
                    "Channel_no": 15,
                    "Channel_Name": "Band 15 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 12.3,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                16: {
                    "Channel_no": 16,
                    "Channel_Name": "Band 16 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.3,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                }
            }
        }
    }
}

Satellites_Objects["goes-17"] = {
    "Satellite_ID": "GOES-17",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES R–T",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 17",
    "Satellite_Status": "Storage",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -137.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -137.0
    },
    "Satellite_Height_Variable_Name": "nominal_satellite_height",
    "Satellite_Nominal_Lat_Variable_Name": "nominal_satellite_subpoint_lat",
    "Satellite_Nominal_Lon_Variable_Name": "nominal_satellite_subpoint_lon",
    "Satellite_Time_Variable_Name": "t",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": 150.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": -55.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": 143.7,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": -53.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Longitude_History_Note": "GOES-17 served as the operational GOES-West satellite (-137.0) from 2019-02-12 until 2023-01-04, when GOES-18 took over. It is now an on-orbit Storage spare at 89.5 W (per NOAA Beginner's Guide to GOES-R Series Data, Sept 2025, p.5), not deactivated hardware -- Nominal_Longitude_Deg below reflects its primary operational slot, not its current storage position.",
    "Operation_Start_Date": "2018-03-01T00:00:00",
    "Operation_End_Date": "2023-01-04T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2018-08-28T00:11:00",
    "Date_Available_End_Date": "2023-01-10T15:29:00",
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "OR_ABI-L1b-RadF-M3C01_G17_s20182400000333_e20182400011100_c20182400011130.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://noaa-goes17.s3.amazonaws.com/ABI-L1b-RadF/2018/240/00/OR_ABI-L1b-RadF-M3C01_G17_s20182400000333_e20182400011100_c20182400011130.nc",
    "File_Path_RegEx": "https://noaa-goes17.s3.amazonaws.com/ABI-L1b-RadF/\\d{4}/\\d{3}/\\d{2}/OR_ABI-L1b-RadF-M3C\\d{2}_G17_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "File_Name_Regex": "OR_ABI-L1b-RadF-M3C\\d{2}_G17_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/goes_17",
                         "https://space.oscar.wmo.int/instruments/view/abi"
                        ],
    "Instruments": {
        "ABI": {
            "ABI_Instrument": "Advanced Baseline Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 16,
            "Full_Disk_Temporal_Resolution_Minutes": 15,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 15,
                "Full_Disk_Scan_Duration_Minutes": None,
                "Mid_Time_Source": "filename",
                "Mid_Time_Method": "netCDF `t` variable is a precomputed J2000 scan mid-time; equivalently (s+e)/2 from the 14-digit sYYYYDDDHHMMSSs/eYYYYDDDHHMMSSs filename fields -- both are the true scan start/end (not product-completion time), per the noaa-goes16 AWS README.",
                "Mid_Time_Confidence": "exact",
            },
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Band 1 - Blue",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.47,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Band 2 - Red",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.64,
                    "Spectral_Interval_Um": 0.1,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 0.5,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Band 3 - Near-Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 0.86,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Band 4 - Cirrus",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.38,
                    "Spectral_Interval_Um": 0.03,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Band 5 - Snow/Ice",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.61,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    # NOAA's own official ABI bands table (goes-r.gov/spacesegment/
                    # ABI-tech-summary.html) gives Band 5 (1.6um Snow/Ice) a
                    # native resolution of 1km, same as Bands 1 and 3 -- only
                    # Band 2 is finer (0.5km) and Band 4 plus Bands 6-16 are
                    # the coarser 2km group. This band is this project's own
                    # Phase-2 SWIR channel (see band_registry.py), so this
                    # value feeds downsample/selection/metadata-disclosure
                    # decisions for every GOES SWIR pixel -- it was previously
                    # listed here as 2.0km, which was wrong.
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                6: {
                    "Channel_no": 6,
                    "Channel_Name": "Band 6 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 2.26,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                7: {
                    "Channel_no": 7,
                    "Channel_Name": "Band 7 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                8: {    
                    "Channel_no": 8,
                    "Channel_Name": "Band 8 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.15,
                    "Spectral_Interval_Um": 0.09,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                9: {
                    "Channel_no": 9,
                    "Channel_Name": "Band 9 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    # NOAA's own official ABI bands table (goes-r.gov/
                    # spacesegment/ABI-tech-summary.html) gives Band 9
                    # ("Midlevel water vapor") a central wavelength of
                    # 6.9um, matching what this file already stores for
                    # Himawari's equivalent WV channel (B09, also 6.9um).
                    # Previously listed here as 7.0um, which was off.
                    "Central_Wavelength_Um": 6.9,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                10: {
                    "Channel_no": 10,
                    "Channel_Name": "Band 10 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 7.4,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                11: {
                    "Channel_no": 11,
                    "Channel_Name": "Band 11 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 8.5,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_INPUT": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                12: {
                    "Channel_no": 12,
                    "Channel_Name": "Band 12 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 9.7,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                13: {
                    "Channel_no": 13,
                    "Channel_Name": "Band 13 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.3,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                14: {
                    "Channel_no": 14,
                    "Channel_Name": "Band 14 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.2,
                    "Spectral_Interval_Um": 0.08,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @  300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                15: {
                    "Channel_no": 15,
                    "Channel_Name": "Band 15 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 12.3,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                16: {
                    "Channel_no": 16,
                    "Channel_Name": "Band 16 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.3,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                }
            }
        }
    }
}

Satellites_Objects["goes-18"] = {
    "Satellite_ID": "GOES-18",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES R–T",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 18",
    "Satellite_Status": "Active",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -137.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -137.0
    },
    "Satellite_Height_Variable_Name": "nominal_satellite_height",
    "Satellite_Nominal_Lat_Variable_Name": "nominal_satellite_subpoint_lat",
    "Satellite_Nominal_Lon_Variable_Name": "nominal_satellite_subpoint_lon",
    "Satellite_Time_Variable_Name": "t",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": 150.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": -55.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": 143.7,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": -53.7
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2022-03-01T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2022-05-11T23:09:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "OR_ABI-L1b-RadF-M6C01_G18_s20221312300197_e20221312309505_c20221312309547.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://noaa-goes18.s3.amazonaws.com/ABI-L1b-RadF/2022/131/23/OR_ABI-L1b-RadF-M6C01_G18_s20221312300197_e20221312309505_c20221312309547.nc",
    "File_Path_RegEx": "https://noaa-goes18.s3.amazonaws.com/ABI-L1b-RadF/\\d{4}/\\d{3}/\\d{2}/OR_ABI-L1b-RadF-M6C\\d{2}_G18_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "File_Name_Regex": "OR_ABI-L1b-RadF-M6C\\d{2}_G18_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/goes_18",
                         "https://space.oscar.wmo.int/instruments/view/abi"
                        ],
    "Instruments": {
        "ABI": {
            "ABI_Instrument": "Advanced Baseline Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 16,
            "Full_Disk_Temporal_Resolution_Minutes": 10,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 10,
                "Full_Disk_Scan_Duration_Minutes": None,
                "Mid_Time_Source": "filename",
                "Mid_Time_Method": "netCDF `t` variable is a precomputed J2000 scan mid-time; equivalently (s+e)/2 from the 14-digit sYYYYDDDHHMMSSs/eYYYYDDDHHMMSSs filename fields -- both are the true scan start/end (not product-completion time), per the noaa-goes16 AWS README.",
                "Mid_Time_Confidence": "exact",
            },
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Band 1 - Blue",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.47,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Band 2 - Red",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.64,
                    "Spectral_Interval_Um": 0.1,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 0.5,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Band 3 - Near-Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 0.86,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Band 4 - Cirrus",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.38,
                    "Spectral_Interval_Um": 0.03,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Band 5 - Snow/Ice",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.61,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    # NOAA's own official ABI bands table (goes-r.gov/spacesegment/
                    # ABI-tech-summary.html) gives Band 5 (1.6um Snow/Ice) a
                    # native resolution of 1km, same as Bands 1 and 3 -- only
                    # Band 2 is finer (0.5km) and Band 4 plus Bands 6-16 are
                    # the coarser 2km group. This band is this project's own
                    # Phase-2 SWIR channel (see band_registry.py), so this
                    # value feeds downsample/selection/metadata-disclosure
                    # decisions for every GOES SWIR pixel -- it was previously
                    # listed here as 2.0km, which was wrong.
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                6: {
                    "Channel_no": 6,
                    "Channel_Name": "Band 6 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 2.26,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                7: {
                    "Channel_no": 7,
                    "Channel_Name": "Band 7 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                8: {
                    "Channel_no": 8,
                    "Channel_Name": "Band 8 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.15,
                    "Spectral_Interval_Um": 0.09,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                9: {
                    "Channel_no": 9,
                    "Channel_Name": "Band 9 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    # NOAA's own official ABI bands table (goes-r.gov/
                    # spacesegment/ABI-tech-summary.html) gives Band 9
                    # ("Midlevel water vapor") a central wavelength of
                    # 6.9um, matching what this file already stores for
                    # Himawari's equivalent WV channel (B09, also 6.9um).
                    # Previously listed here as 7.0um, which was off.
                    "Central_Wavelength_Um": 6.9,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                10: {
                    "Channel_no": 10,
                    "Channel_Name": "Band 10 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 7.4,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                11: {
                    "Channel_no": 11,
                    "Channel_Name": "Band 11 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 8.5,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_INPUT": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                12: {
                    "Channel_no": 12,
                    "Channel_Name": "Band 12 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 9.7,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                13: {
                    "Channel_no": 13,
                    "Channel_Name": "Band 13 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.3,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                14: {
                    "Channel_no": 14,
                    "Channel_Name": "Band 14 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.2,
                    "Spectral_Interval_Um": 0.08,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                15: {
                    "Channel_no": 15,
                    "Channel_Name": "Band 15 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 12.3,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                16: {
                    "Channel_no": 16,
                    "Channel_Name": "Band 16 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.3,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                }
            }
        }
    }
}

Satellites_Objects["goes-19"] = {
    "Satellite_ID": "GOES-19",
    "Operator": "NOAA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "GOES R–T",
    "Satellite_Full_Name": "Geostationary Operational Environmental Satellite - 19",
    "Satellite_Status": "Active",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": -75.2,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": -75.2
    },
    "Satellite_Height_Variable_Name": "nominal_satellite_height",
    "Satellite_Nominal_Lat_Variable_Name": "nominal_satellite_subpoint_lat",
    "Satellite_Nominal_Lon_Variable_Name": "nominal_satellite_subpoint_lon",
    "Satellite_Time_Variable_Name": "t",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -180.0,
        "lat_max": 85.0,
        "lat_min": -85.0,
        "lon_max": 5.0
    },
    "Footprint_Bounding_Box": {
        "lon_min": -156.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 6.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Longitude_History_Note": "GOES-19 launched 2024-06-25 and became the operational GOES-East satellite (75.2 W) on 2025-04-07, taking over from GOES-16. Prior to 2025-04-07 it was in post-launch checkout, not in the West slot -- the -137.0 value previously in this entry was a copy-paste error from the goes-18/goes-17 (GOES-West) block. Corrected 2026-07-30 per NOAA Beginner's Guide to GOES-R Series Data (Sept 2025, p.5) and OSCAR (space.oscar.wmo.int/satellites/view/goes_19). Operation_Start_Date below follows this file's launch-date convention (matching goes-16/17/18's use of launch date, not slot-assignment date); see this note for the East-slot handover date.",
    "Operation_Start_Date": "2024-06-25T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2024-10-10T23:09:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://www.ncei.noaa.gov/data/gridsat-goes/access/goes/",
    "Filename_Example": "OR_ABI-L1b-RadF-M6C01_G19_s20242841940203_e20242841949511_c20242841949563.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://noaa-goes19.s3.amazonaws.com/ABI-L1b-RadF/2024/284/19/OR_ABI-L1b-RadF-M6C01_G19_s20242841940203_e20242841949511_c20242841949563.nc",
    "File_Path_RegEx": "https://noaa-goes19.s3.amazonaws.com/ABI-L1b-RadF/\\d{4}/\\d{3}/\\d{2}/OR_ABI-L1b-RadF-M6C\\d{2}_G19_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "File_Name_Regex": "OR_ABI-L1b-RadF-M6C\\d{2}_G19_s\\d{10}_e\\d{10}_c\\d{10}.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/goes_19",
                         "https://space.oscar.wmo.int/instruments/view/abi"
                        ],
    "Instruments": {
        "ABI": {
            "ABI_Instrument": "Advanced Baseline Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 16,
            "Full_Disk_Temporal_Resolution_Minutes": 10,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 10,
                "Full_Disk_Scan_Duration_Minutes": None,
                "Mid_Time_Source": "filename",
                "Mid_Time_Method": "netCDF `t` variable is a precomputed J2000 scan mid-time; equivalently (s+e)/2 from the 14-digit sYYYYDDDHHMMSSs/eYYYYDDDHHMMSSs filename fields -- both are the true scan start/end (not product-completion time), per the noaa-goes16 AWS README.",
                "Mid_Time_Confidence": "exact",
            },
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "Band 1 - Blue",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.47,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "Band 2 - Red",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.64,
                    "Spectral_Interval_Um": 0.1,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 0.5,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "Band 3 - Near-Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 0.86,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "Band 4 - Cirrus",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.38,
                    "Spectral_Interval_Um": 0.03,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "Band 5 - Snow/Ice",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 1.61,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    # NOAA's own official ABI bands table (goes-r.gov/spacesegment/
                    # ABI-tech-summary.html) gives Band 5 (1.6um Snow/Ice) a
                    # native resolution of 1km, same as Bands 1 and 3 -- only
                    # Band 2 is finer (0.5km) and Band 4 plus Bands 6-16 are
                    # the coarser 2km group. This band is this project's own
                    # Phase-2 SWIR channel (see band_registry.py), so this
                    # value feeds downsample/selection/metadata-disclosure
                    # decisions for every GOES SWIR pixel -- it was previously
                    # listed here as 2.0km, which was wrong.
                    "Spatial_Resolution_Km": 1.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                6: {
                    "Channel_no": 6,
                    "Channel_Name": "Band 6 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 2.26,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "300 @ 100 % albedo",
                    "Spatial_Resolution_Km": 2.0,
                    "Radiance_Conversion_Required": True,
                    "Radiance_Conversion_Formula": "kappa0 * Rad"
                },
                7: {
                    "Channel_no": 7,
                    "Channel_Name": "Band 7 - Shortwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                8: {
                    "Channel_no": 8,
                    "Channel_Name": "Band 8 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.15,
                    "Spectral_Interval_Um": 0.09,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                9: {
                    "Channel_no": 9,
                    "Channel_Name": "Band 9 - Water Vapor",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Water Vapor",
                    # NOAA's own official ABI bands table (goes-r.gov/
                    # spacesegment/ABI-tech-summary.html) gives Band 9
                    # ("Midlevel water vapor") a central wavelength of
                    # 6.9um, matching what this file already stores for
                    # Himawari's equivalent WV channel (B09, also 6.9um).
                    # Previously listed here as 7.0um, which was off.
                    "Central_Wavelength_Um": 6.9,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                10: {
                    "Channel_no": 10,
                    "Channel_Name": "Band 10 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 7.4,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                11: {
                    "Channel_no": 11,
                    "Channel_Name": "Band 11 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 8.5,
                    "Spectral_Interval_Um": 0.04,
                    "SNR_OR_NEΔT_AT_Specified_INPUT": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                12: {
                    "Channel_no": 12,
                    "Channel_Name": "Band 12 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 9.7,
                    "Spectral_Interval_Um": 0.02,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                13: {
                    "Channel_no": 13,
                    "Channel_Name": "Band 13 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 10.3,
                    "Spectral_Interval_Um": 0.05,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                14: {
                    "Channel_no": 14,
                    "Channel_Name": "Band 14 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 11.2,
                    "Spectral_Interval_Um": 0.08,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                15: {
                    "Channel_no": 15,
                    "Channel_Name": "Band 15 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 12.3,
                    "Spectral_Interval_Um": 1.0,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                },
                16: {
                    "Channel_no": 16,
                    "Channel_Name": "Band 16 - Longwave Infrared",
                    "Channel_Band_Variable_Name": "Rad",
                    "Band_Wavelength_Variable_Name": "band_wavelength",
                    "Channel_Type": "Longwave Infrared",
                    "Central_Wavelength_Um": 13.3,
                    "Spectral_Interval_Um": 0.06,
                    "SNR_OR_NEΔT_AT_Specified_Input": "0.1 K @ 300 K",
                    "Spatial_Resolution_Km": 2.0,
                    "Brightness_Temperature_Conversion_Required": True,
                    "Brightness_Temperature_Conversion_Formula": "(planck_fk2 / np.log((planck_fk1 / Rad) + 1) - planck_bc1) / planck_bc2"
                }
            }
        }
    }
}

Satellites_Objects["meteosat-05"] = {
    "Satellite_ID": "Meteosat-05",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat First Generation (MFG)",
    "Satellite_Full_Name": "Meteosat-5",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 0.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 0.0
    },
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Footprint_Bounding_Box": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "1991-03-02T00:00:00",
    "Operation_End_Date": "1998-06-01T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "1994-02-04T09:00:00",
    "Date_Available_End_Date": "1997-02-13T07:30:00",
    "Data_Dissemination_From": "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0880&si=0&c=20&sort=start,time,0",
    "Filename_Example": "MVIRI_FCDR-EASY_L15_MET5-E6300_199807010000_199807010030_0200.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0880/products/MVIRI_FCDR-EASY_L15_MET5-E0000_199402040900_199402040930_0200/entry?name=MVIRI_FCDR-EASY_L15_MET5-E0000_199402040900_199402040930_0200.nc&access_token=REDACTED",
    "File_Path_RegEx": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0880/products/MVIRI_FCDR-EASY_L15_MET5-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200/entry?name=MVIRI_FCDR-EASY_L15_MET5-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc&access_token=[a-zA-Z0-9-]+",
    "File_Name_Regex": "MVIRI_FCDR-EASY_L15_MET5-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_05",
                         "https://space.oscar.wmo.int/instruments/view/mviri",
                         "https://data.eumetsat.int/data/map/EO:EUM:DAT:0880?start=1994-02-04T09:00:00.000Z&end=1997-02-13T07:30:00.000Z&sort=start,time,0",
                         "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0880&dtstart=1994-02-04T09%3A00%3A00Z&dtend=1997-02-13T07%3A30%3A00Z&sort=start,time,0&si=0&c=20&"
                        ],
    "Instruments": {
        "MVIRI": {
            "MVIRI_Instrument": "Meteosat Visible Infra-Red Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 3,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "Visible",
                        "Channel_Band_Variable_Name": "toa_bidirectional_reflectance_vis",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.7,
                        "Spectral_Interval_Um": 0.4,
                        "SNR_OR_NEΔT_AT_Specified_Input": "3.0 @ 1 % albedo",
                        "Spatial_Resolution_Km": 2.5
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "Water Vapor",
                        "Channel_Band_Variable_Name": "count_wv",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 6.4,
                        "Spectral_Interval_Um": 1.4,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "1.0 K @ 250 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_wv * count_wv + b_wv",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_wv * np.log(a_wv * count_wv + b_wv) + bt_b_wv"
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "Thermal Infrared",
                        "Channel_Band_Variable_Name": "count_ir",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 11.5,
                        "Spectral_Interval_Um": 2.0,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "0.5 K @ 300 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_ir * count_ir + b_ir",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_ir * np.log(a_ir * count_ir + b_ir) + bt_b_ir"
                    }
            }
        }
    }
}

Satellites_Objects["meteosat-05 (IODC)"] = {
    "Satellite_ID": "Meteosat-05 (Indian Ocean Data Coverage)",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat First Generation (MFG)",
    "Satellite_Full_Name": "Meteosat-5",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 63.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 63.0
    },
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -18.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 144.3
    },
    "Footprint_Bounding_Box": {
        "lon_min": -18.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 144.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "1998-06-01T00:00:00",
    "Operation_End_Date": "2007-04-26T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "1998-07-01T00:00:00",
    "Date_Available_End_Date": "2006-10-31T23:30:00",
    "Data_Dissemination_From": "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0882&si=0&c=20&sort=start,time,0",
    "Filename_Example": "MVIRI_FCDR-EASY_L15_MET5-E6300_199807010000_199807010030_0200.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0882/products/MVIRI_FCDR-EASY_L15_MET5-E6300_199807010000_199807010030_0200/entry?name=MVIRI_FCDR-EASY_L15_MET5-E6300_199807010000_199807010030_0200.nc&access_token=REDACTED",
    "File_Path_RegEx": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0882/products/MVIRI_FCDR-EASY_L15_MET5-E6300_\\d{8}\\d{4}_\\d{8}\\d{4}_0200/entry?name=MVIRI_FCDR-EASY_L15_MET5-E6300_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc&access_token=[a-zA-Z0-9-]+",
    "File_Name_Regex": "MVIRI_FCDR-EASY_L15_MET5-E6300_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_05",
                         "https://space.oscar.wmo.int/instruments/view/mviri",
                         "https://data.eumetsat.int/data/map/EO:EUM:DAT:0882?start=1998-07-01T00:00:00.000Z&end=2006-10-31T23:30:00.000Z&sort=start,time,0",
                         "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0882&dtstart=1998-07-01T00%3A00%3A00Z&dtend=2006-10-31T23%3A30%3A00Z&sort=start,time,0&si=0&c=20&"
                        ],
    "Instruments": {
        "MVIRI": {
            "MVIRI_Instrument": "Meteosat Visible Infra-Red Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 3,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "Visible",
                        "Channel_Band_Variable_Name": "toa_bidirectional_reflectance_vis",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.7,
                        "Spectral_Interval_Um": 0.4,
                        "SNR_OR_NEΔT_AT_Specified_Input": "3.0 @ 1 % albedo",
                        "Spatial_Resolution_Km": 2.5
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "Water Vapor",
                        "Channel_Band_Variable_Name": "count_wv",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 6.4,
                        "Spectral_Interval_Um": 1.4,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "1.0 K @ 250 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_wv * count_wv + b_wv",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_wv * np.log(a_wv * count_wv + b_wv) + bt_b_wv"
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "Thermal Infrared",
                        "Channel_Band_Variable_Name": "count_ir",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 11.5,
                        "Spectral_Interval_Um": 2.0,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "0.5 K @ 300 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_ir * count_ir + b_ir",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_ir * np.log(a_ir * count_ir + b_ir) + bt_b_ir"
                    }
            }
        }
    }
}

Satellites_Objects["meteosat-06"] = {
    "Satellite_ID": "Meteosat-06",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat First Generation (MFG)",
    "Satellite_Full_Name": "Meteosat-6",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 0.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 0.0
    },
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Footprint_Bounding_Box": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "1993-11-20T00:00:00",
    "Operation_End_Date": "2007-04-27T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "1997-02-13T08:30:00",
    "Date_Available_End_Date": "1998-06-03T07:30:00",
    "Data_Dissemination_From": "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0880&si=0&c=20&sort=start,time,0",
    "Filename_Example": "MVIRI_FCDR-EASY_L15_MET6-E0000_199702130830_199702130900_0200.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0880/products/MVIRI_FCDR-EASY_L15_MET6-E0000_199702130830_199702130900_0200/entry?name=MVIRI_FCDR-EASY_L15_MET6-E0000_199702130830_199702130900_0200.nc&access_token=REDACTED",
    "File_Path_RegEx": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0880/products/MVIRI_FCDR-EASY_L15_MET6-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200/entry?name=MVIRI_FCDR-EASY_L15_MET6-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc&access_token=[a-zA-Z0-9-]+",
    "File_Name_Regex": "MVIRI_FCDR-EASY_L15_MET6-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_06",
                         "https://space.oscar.wmo.int/instruments/view/mviri",
                         "https://data.eumetsat.int/data/map/EO:EUM:DAT:0880?start=1997-02-13T08:30:00.000Z&end=1998-06-03T07:30:00.000Z&sort=start,time,0",
                         "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0880&dtstart=1997-02-13T08%3A30%3A00Z&dtend=1998-06-03T07%3A30%3A00Z&sort=start,time,0&si=0&c=20&"
                        ],
    "Instruments": {
        "MVIRI": {
            "MVIRI_Instrument": "Meteosat Visible Infra-Red Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 3,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "Visible",
                        "Channel_Band_Variable_Name": "toa_bidirectional_reflectance_vis",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.7,
                        "Spectral_Interval_Um": 0.4,
                        "SNR_OR_NEΔT_AT_Specified_Input": "3.0 @ 1 % albedo",
                        "Spatial_Resolution_Km": 2.5
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "Water Vapor",
                        "Channel_Band_Variable_Name": "count_wv",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 6.4,
                        "Spectral_Interval_Um": 1.4,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "1.0 K @ 250 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_wv * count_wv + b_wv",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_wv * np.log(a_wv * count_wv + b_wv) + bt_b_wv"
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "Thermal Infrared",
                        "Channel_Band_Variable_Name": "count_ir",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 11.5,
                        "Spectral_Interval_Um": 2.0,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "0.5 K @ 300 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_ir * count_ir + b_ir",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_ir * np.log(a_ir * count_ir + b_ir) + bt_b_ir"
                    }
            }
        }
    }
}

Satellites_Objects["meteosat-06 (IODC)"] = {
    "Satellite_ID": "Meteosat-06 (Indian Ocean Data Coverage)",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat First Generation (MFG)",
    "Satellite_Full_Name": "Meteosat-6",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 63.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 63.0
    },
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -18.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 144.3
    },
    "Footprint_Bounding_Box": {
        "lon_min": -18.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 144.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2007-04-27T00:00:00",
    "Operation_End_Date": "2011-04-15T00:00:00",
    "Data_Available": False,
    "Data_Available_Start_Date": None,
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": None,
    "Filename_Example": None,
    "File_Type": None,
    "File_Path_Example": None,
    "File_Path_Regex": None,
    "File_Name_Regex": None,
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_06",
                         "https://space.oscar.wmo.int/instruments/view/mviri"
                        ],
    "Instruments": {
        "MVIRI": {
            "MVIRI_Instrument": "Meteosat Visible Infra-Red Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 3,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "Visible",
                        "Channel_Band_Variable_Name": "toa_bidirectional_reflectance_vis",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.7,
                        "Spectral_Interval_Um": 0.4,
                        "SNR_OR_NEΔT_AT_Specified_Input": "3.0 @ 1 % albedo",
                        "Spatial_Resolution_Km": 2.5
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "Water Vapor",
                        "Channel_Band_Variable_Name": "count_wv",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 6.4,
                        "Spectral_Interval_Um": 1.4,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "1.0 K @ 250 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_wv * count_wv + b_wv",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_wv * np.log(a_wv * count_wv + b_wv) + bt_b_wv"
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "Thermal Infrared",
                        "Channel_Band_Variable_Name": "count_ir",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 11.5,
                        "Spectral_Interval_Um": 2.0,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "0.5 K @ 300 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_ir * count_ir + b_ir",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_ir * np.log(a_ir * count_ir + b_ir) + bt_b_ir"
                    }
            }
        }
    }
}

Satellites_Objects["meteosat-07"] = {
    "Satellite_ID": "Meteosat-07",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat First Generation (MFG)",
    "Satellite_Full_Name": "Meteosat-7",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 0.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 0.0
    },
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Footprint_Bounding_Box": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "1997-09-02T00:00:00",
    "Operation_End_Date": "2006-12-05T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "1998-06-03T23:00:00",
    "Date_Available_End_Date": "2006-07-19T23:30:00",
    "Data_Dissemination_From": "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0880&si=0&c=20&sort=start,time,0",
    "Filename_Example": "MVIRI_FCDR-EASY_L15_MET7-E0000_199806032300_199806032330_0200.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0880/products/MVIRI_FCDR-EASY_L15_MET7-E0000_199806032300_199806032330_0200/entry?name=MVIRI_FCDR-EASY_L15_MET7-E0000_199806032300_199806032330_0200.nc&access_token=REDACTED",
    "File_Path_RegEx": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0880/products/MVIRI_FCDR-EASY_L15_MET7-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200/entry?name=MVIRI_FCDR-EASY_L15_MET7-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc&access_token=[a-zA-Z0-9-]+",
    "File_Name_Regex": "MVIRI_FCDR-EASY_L15_MET7-E0000_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_07",
                         "https://space.oscar.wmo.int/instruments/view/mviri",
                         "https://data.eumetsat.int/data/map/EO:EUM:DAT:0880?start=1998-06-03T23:00:00.000Z&end=2006-07-19T23:30:00.000Z&sort=start,time,0",
                         "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0880&dtstart=1997-06-03T23%3A00%3A00Z&dtend=2006-07-19T23%3A30%3A00Z&sort=start,time,0&si=0&c=20&"
                        ],
    "Instruments": {
        "MVIRI": {
            "MVIRI_Instrument": "Meteosat Visible Infra-Red Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 3,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "Visible",
                        "Channel_Band_Variable_Name": "toa_bidirectional_reflectance_vis",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.7,
                        "Spectral_Interval_Um": 0.4,
                        "SNR_OR_NEΔT_AT_Specified_Input": "3.0 @ 1 % albedo",
                        "Spatial_Resolution_Km": 2.5
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "Water Vapor",
                        "Channel_Band_Variable_Name": "count_wv",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 6.4,
                        "Spectral_Interval_Um": 1.4,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "1.0 K @ 250 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_wv * count_wv + b_wv",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_wv * np.log(a_wv * count_wv + b_wv) + bt_b_wv"
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "Thermal Infrared",
                        "Channel_Band_Variable_Name": "count_ir",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 11.5,
                        "Spectral_Interval_Um": 2.0,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "0.5 K @ 300 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_ir * count_ir + b_ir",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_ir * np.log(a_ir * count_ir + b_ir) + bt_b_ir"
                    }
            }
        }
    }
}

Satellites_Objects["meteosat-07 (IODC)"] = {
    "Satellite_ID": "Meteosat-07 (Indian Ocean Data Coverage)",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat First Generation (MFG)",
    "Satellite_Full_Name": "Meteosat-7",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 57.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 57.0
    },
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -24.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 138.3
    },
    "Footprint_Bounding_Box": {
        "lon_min": -24.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 138.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2006-12-05T00:00:00",
    "Operation_End_Date": "2017-02-01T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2006-11-01T00:00:00",
    "Date_Available_End_Date": "2017-04-04T23:30:00",
    "Data_Dissemination_From": "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0881&si=0&c=20&sort=start,time,0",
    "Filename_Example": "MVIRI_FCDR-EASY_L15_MET7-E5700_200611010000_200611010030_0200.nc",
    "File_Type": "NetCDF",
    "File_Path_Example": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0881/products/MVIRI_FCDR-EASY_L15_MET7-E5700_200611010000_200611010030_0200/entry?name=MVIRI_FCDR-EASY_L15_MET7-E5700_200611010000_200611010030_0200.nc&access_token=REDACTED",
    "File_Path_Regex": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3A0881/products/MVIRI_FCDR-EASY_L15_MET7-E5700_\\d{8}\\d{4}_\\d{8}\\d{4}_0200/entry?name=MVIRI_FCDR-EASY_L15_MET7-E5700_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc&access_token=[a-zA-Z0-9-]+",
    "File_Name_Regex": "MVIRI_FCDR-EASY_L15_MET7-E5700_\\d{8}\\d{4}_\\d{8}\\d{4}_0200.nc",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_07",
                         "https://space.oscar.wmo.int/instruments/view/mviri",
                         "https://data.eumetsat.int/data/map/EO:EUM:DAT:0881?start=2006-11-01T00:00:00.000Z&end=2017-04-04T23:30:00.000Z&sort=start,time,0",
                         "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:0881&dtstart=2006-11-01T00%3A00%3A00Z&dtend=2017-04-04T23%3A30%3A00Z&sort=start,time,0&si=0&c=20&"
                        ],
    "Instruments": {
        "MVIRI": {
            "MVIRI_Instrument": "Meteosat Visible Infra-Red Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 3,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "Visible",
                        "Channel_Band_Variable_Name": "toa_bidirectional_reflectance_vis",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.7,
                        "Spectral_Interval_Um": 0.4,
                        "SNR_OR_NEΔT_AT_Specified_Input": "3.0 @ 1 % albedo",
                        "Spatial_Resolution_Km": 2.5
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "Water Vapor",
                        "Channel_Band_Variable_Name": "count_wv",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 6.4,
                        "Spectral_Interval_Um": 1.4,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "1.0 K @ 250 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_wv * count_wv + b_wv",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_wv * np.log(a_wv * count_wv + b_wv) + bt_b_wv"
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "Thermal Infrared",
                        "Channel_Band_Variable_Name": "count_ir",
                        "Band_Wavelength_Variable_Name": "channel_wavelength",
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 11.5,
                        "Spectral_Interval_Um": 2.0,
                        "SNR_OR_NEΔT_AT_Specified_INPUT": "0.5 K @ 300 K",
                        "Spatial_Resolution_Km": 5.0,
                        "Radiance_Conversion_Required": True,
                        "Radiance_Conversion_Formula": "a_ir * count_ir + b_ir",
                        "Brightness_Temperature_Conversion_Required": True,
                        "Brightness_Temperature_Conversion_Formula": "bt_a_ir * np.log(a_ir * count_ir + b_ir) + bt_b_ir"
                    }
            }
        }
    }
}

Satellites_Objects["meteosat-08"] = {
    "Satellite_ID": "Meteosat-08",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat Second Generation (MSG)",
    "Satellite_Full_Name": "Meteosat-8",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 0.0,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 0.0
    },
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Footprint_Bounding_Box": {
        "lon_min": -81.3,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 81.3
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2002-08-28T00:00:00",
    "Operation_End_Date": "2016-07-04T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2004-02-24T00:00:00",
    "Date_Available_End_Date": "2016-05-04T11:30:00",
    "Data_Dissemination_From": "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:MSG:HRSEVIRI&si=0&c=20&sort=start,time,0&sat=MSG1&type=MSG15&title=*MSG1*",
    "Filename_Example": "MSG1-SEVI-MSG15-0201-NA-20040119104236.059000000Z-NA.nat",
    "File_Type": "Native Binary",
    "File_Path_Example": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3AMSG%3AHRSEVIRI/products/MSG1-SEVI-MSG15-0201-NA-20040119104236.059000000Z-NA/entry?name=MSG1-SEVI-MSG15-0201-NA-20040119104236.059000000Z-NA.nat&access_token=REDACTED",
    "File_Path_RegEx": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3AMSG%3AHRSEVIRI/products/MSG1-SEVI-MSG15-0201-NA-\\d{8}T\\d{6}\\.\\d{6}Z-NA/entry?name=MSG1-SEVI-MSG15-0201-NA-\\d{8}T\\d{6}\\.\\d{6}Z-NA.nat&access_token=[a-zA-Z0-9-]+",
    "File_Name_Regex": "MSG1-SEVI-MSG15-0201-NA-\\d{8}T\\d{6}\\.\\d{6}Z-NA.nat",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_08",
                         "https://space.oscar.wmo.int/instruments/view/seviri",
                         "https://data.eumetsat.int/data/map/EO:EUM:DAT:MSG:HRSEVIRI?start=2004-01-19T10:30:03.505Z&end=2016-05-04T11:30:00.000Z&facets=sat%7CMSG1,type%7CMSG15&sort=start,time,0&productName=*MSG1*",
                         "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO%3AEUM%3ADAT%3AMSG%3AHRSEVIRI&si=0&c=20&sort=start%2Ctime%2C0&dtstart=2004-01-19T10%3A30%3A03Z&dtend=2026-03-22T16%3A57%3A42Z&sat=MSG1&type=MSG15&title=*MSG1*"
                        ],
    "Instruments": {
        "SEVIRI": {
            "SEVIRI_Instrument": "Spinning Enhanced Visible and Infrared Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 12,
            "Full_Disk_Temporal_Resolution_Minutes": 15,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 15,
                "Full_Disk_Scan_Duration_Minutes": 12.5,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Best: Trailer record ImageProductionStats.ActualScanningSummary.ForwardScanStart/ForwardScanEnd, averaged -- the actual observed first/last forward-scan line times. Best-of-all (true per-pixel accuracy): per-line L10LineMeanAcquisitionTime field, which captures the real south-to-north acquisition-time gradient across the disk. Filename-only approximation: filename_time - 6.25 min (the native-format filename timestamp is the true scan END time, checked 2026-07-30 against a real MSG3 file; half the ~12.5 min imaging window is subtracted, not added, and not half the full 15-min repeat cycle).",
                "Mid_Time_Confidence": "approximate",
            },
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "VIS0.6 - Visible",
                        "Channel_Band_Variable_Name": "VIS006",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.635,
                        "Spectral_Interval_Um": 0.15,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "VIS0.8 - Visible",
                        "Channel_Band_Variable_Name": "VIS008",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.81,
                        "Spectral_Interval_Um": 0.14,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo (VIS0.6 figure re-used as an approximation -- per-channel SNR not sourced in this research pass)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "IR1.6 - Near-Infrared (this project's Phase-2 Shortwave IR)",
                        "Channel_Band_Variable_Name": "NIR016",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Shortwave Infrared",
                        "Central_Wavelength_Um": 1.64,
                        "Spectral_Interval_Um": 0.28,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo (VIS0.6 figure re-used as an approximation -- per-channel SNR not sourced in this research pass)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    4: {
                        "Channel_no": 4,
                        "Channel_Name": "IR3.9 - Shortwave IR window (this project's Phase-2 Mid IR)",
                        "Channel_Band_Variable_Name": "WV039",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Shortwave Infrared",
                        # EUMETSAT's own official SEVIRI channel table
                        # (introductory MSG document, eumetsat.int) gives
                        # IR3.9's central wavelength as 3.90um, not the
                        # 3.92um previously listed here.
                        "Central_Wavelength_Um": 3.90,
                        "Spectral_Interval_Um": 0.88,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this channel (a mixed solar/thermal window channel; needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    5: {
                        "Channel_no": 5,
                        "Channel_Name": "WV6.2 - Water Vapor (this project's Phase-2 Water vapor)",
                        "Channel_Band_Variable_Name": "WV062",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Water Vapor",
                        "Central_Wavelength_Um": 6.25,
                        "Spectral_Interval_Um": 1.80,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    6: {
                        "Channel_no": 6,
                        "Channel_Name": "WV7.3 - Water Vapor",
                        "Channel_Band_Variable_Name": "WV073",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Water Vapor",
                        "Central_Wavelength_Um": 7.35,
                        "Spectral_Interval_Um": 1.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    7: {
                        "Channel_no": 7,
                        "Channel_Name": "IR8.7 - Infrared",
                        "Channel_Band_Variable_Name": "IR087",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 8.70,
                        "Spectral_Interval_Um": 0.80,
                        "SNR_OR_NEΔT_AT_Specified_Input": "0.17-0.28 K @ 300 K (measured range, MSG-1, Schmetz2000 Table 2)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    8: {
                        "Channel_no": 8,
                        "Channel_Name": "IR9.7 - Infrared (ozone, experimental)",
                        "Channel_Band_Variable_Name": "IR097",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 9.66,
                        "Spectral_Interval_Um": 0.56,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    9: {
                        "Channel_no": 9,
                        "Channel_Name": "IR10.8 - IR window, clean (this project's Phase-0/2 IR window, clean)",
                        "Channel_Band_Variable_Name": "IR108",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 10.80,
                        "Spectral_Interval_Um": 2.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": "0.15 K @ 300 K (measured, MSG-1; spec requirement 0.25 K @ 300 K, Schmetz2000 Table 2)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    10: {
                        "Channel_no": 10,
                        "Channel_Name": "IR12.0 - IR window, dirty (this project's Phase-2 IR window, dirty)",
                        "Channel_Band_Variable_Name": "IR120",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 12.00,
                        "Spectral_Interval_Um": 2.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    11: {
                        "Channel_no": 11,
                        "Channel_Name": "IR13.4 - Infrared (CO2 absorption)",
                        "Channel_Band_Variable_Name": "IR134",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 13.40,
                        "Spectral_Interval_Um": 2.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": "0.30 K @ 270 K (measured, MSG-1; spec requirement 1.80 K @ 270 K, Schmetz2000 Table 2)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    12: {
                        "Channel_no": 12,
                        "Channel_Name": "HRV - High-Resolution Visible",
                        "Channel_Band_Variable_Name": "HRV",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.75,
                        "Spectral_Interval_Um": 0.30,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo (VIS0.6 figure re-used as an approximation -- per-channel SNR not sourced in this research pass)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 1.6,
                            "Native_Sampling_Km": 1.0,
                            "Dataset_Resolution_Km": 1.0
                        }
                    }
            }
        }
    }
}

Satellites_Objects["meteosat-09"] = {
    "Satellite_ID": "Meteosat-09",
    "Operator": "EUMETSAT",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat Second Generation (MSG)",
    "Satellite_Full_Name": "Meteosat-9",
    "Satellite_Status": "Operational (IODC)",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 45.5,
    "Sub_Satellite_Point": {
        "lat": 0.0,
        "lon": 45.5
    },
    "Longitude_History_Note": (
        "Served as Prime (0E) 2005-01-05 to 2013-01-21; moved off Prime after "
        "Meteosat-10 took over; became the Indian Ocean Data Coverage (IODC) "
        "satellite at 45.5E on 2022-07-01, replacing Meteosat-8 (previously "
        "IODC at 41.5E, decommissioned Nov 2022), current. NOTE: this is a "
        "different EUMETSAT service from the Rapid Scan Service backup at "
        "9.5E (that is meteosat-11) -- the two were previously conflated in "
        "this project's README/config, which had no entry at all for the "
        "real IODC occupant. Corrected 2026-07-30 per space.oscar.wmo.int/"
        "satellites/view/meteosat_9 and /meteosat_9_iodc, and the EUMETSAT "
        "MSG SEVIRI level 1.5 data guide V1.1 (25 Mar 2025), p.10-11."
    ),
    "Satellite_Height_Variable_Name": None,
    "Satellite_Nominal_Lat_Variable_Name": None,
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Spatial_Bounding_Box_Ref": ["lon_min(W)", "lat_max(N)", "lat_min(S)", "lon_max(E)"],
    "Dataset_Grid_Extent": {
        "lon_min": -35.8,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 126.8
    },
    "Footprint_Bounding_Box": {
        "lon_min": -35.8,
        "lat_max": 66.5,
        "lat_min": -66.5,
        "lon_max": 126.8
    },
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2005-01-05T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2005-01-05T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO:EUM:DAT:MSG%3AHRSEVIRI&si=0&c=20&sort=start%2Ctime%2C0&sat=MSG2&type=MSG15&title=*MSG2*",
    "Filename_Example": "MSG2-SEVI-MSG15-0201-NA-20050105100000.000000000Z-NA.nat",
    "File_Type": "Native Binary",
    "File_Path_Example": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3AMSG%3AHRSEVIRI/products/MSG2-SEVI-MSG15-0201-NA-20050105100000.000000000Z-NA/entry?name=MSG2-SEVI-MSG15-0201-NA-20050105100000.000000000Z-NA.nat&access_token=REDACTED",
    "File_Path_RegEx": "https://api.eumetsat.int/data/download/1.0.0/collections/EO%3AEUM%3ADAT%3AMSG%3AHRSEVIRI/products/MSG2-SEVI-MSG15-0201-NA-\\d{8}T\\d{6}\\.\\d{6}Z-NA/entry?name=MSG2-SEVI-MSG15-0201-NA-\\d{8}T\\d{6}\\.\\d{6}Z-NA.nat&access_token=[a-zA-Z0-9-]+",
    "File_Name_Regex": "MSG2-SEVI-MSG15-0201-NA-\\d{8}T\\d{6}\\.\\d{6}Z-NA.nat",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_09",
                         "https://space.oscar.wmo.int/instruments/view/seviri",
                         "https://data.eumetsat.int/data/map/EO:EUM:DAT:MSG:HRSEVIRI?start=2005-01-05T00%3A00%3A00Z&end=2026-03-22T16%3A57%3A42Z&facets=sat%7CMSG2,type%7CMSG15&sort=start%2Ctime%2C0&productName=*MSG2*",
                         "https://api.eumetsat.int/data/search-products/1.0.0/os?format=json&pi=EO%3AEUM%3ADAT%3AMSG%3AHRSEVIRI&si=0&c=20&sort=start%2Ctime%2C0&dtstart=2005-01-05T00%3A00%3A00Z&dtend=2026-03-22T16%3A57%3A42Z&sat=MSG2&type=MSG15&title=*MSG2*"
                        ],
    "Instruments": {
        "SEVIRI": {
            "SEVIRI_Instrument": "Spinning Enhanced Visible and Infrared Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 12,
            "Full_Disk_Temporal_Resolution_Minutes": 15,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 15,
                "Full_Disk_Scan_Duration_Minutes": 12.5,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Best: Trailer record ImageProductionStats.ActualScanningSummary.ForwardScanStart/ForwardScanEnd, averaged -- the actual observed first/last forward-scan line times. Best-of-all (true per-pixel accuracy): per-line L10LineMeanAcquisitionTime field, which captures the real south-to-north acquisition-time gradient across the disk. Filename-only approximation: filename_time - 6.25 min (the native-format filename timestamp is the true scan END time, checked 2026-07-30 against a real MSG3 file; half the ~12.5 min imaging window is subtracted, not added, and not half the full 15-min repeat cycle).",
                "Mid_Time_Confidence": "approximate",
            },
            "Channels": {
                    1: {
                        "Channel_no": 1,
                        "Channel_Name": "VIS0.6 - Visible",
                        "Channel_Band_Variable_Name": "VIS006",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.635,
                        "Spectral_Interval_Um": 0.15,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    2: {
                        "Channel_no": 2,
                        "Channel_Name": "VIS0.8 - Visible",
                        "Channel_Band_Variable_Name": "VIS008",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.81,
                        "Spectral_Interval_Um": 0.14,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo (VIS0.6 figure re-used as an approximation -- per-channel SNR not sourced in this research pass)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    3: {
                        "Channel_no": 3,
                        "Channel_Name": "IR1.6 - Near-Infrared (this project's Phase-2 Shortwave IR)",
                        "Channel_Band_Variable_Name": "NIR016",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Shortwave Infrared",
                        "Central_Wavelength_Um": 1.64,
                        "Spectral_Interval_Um": 0.28,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo (VIS0.6 figure re-used as an approximation -- per-channel SNR not sourced in this research pass)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    4: {
                        "Channel_no": 4,
                        "Channel_Name": "IR3.9 - Shortwave IR window (this project's Phase-2 Mid IR)",
                        "Channel_Band_Variable_Name": "WV039",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Shortwave Infrared",
                        # EUMETSAT's own official SEVIRI channel table
                        # (introductory MSG document, eumetsat.int) gives
                        # IR3.9's central wavelength as 3.90um, not the
                        # 3.92um previously listed here.
                        "Central_Wavelength_Um": 3.90,
                        "Spectral_Interval_Um": 0.88,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this channel (a mixed solar/thermal window channel; needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    5: {
                        "Channel_no": 5,
                        "Channel_Name": "WV6.2 - Water Vapor (this project's Phase-2 Water vapor)",
                        "Channel_Band_Variable_Name": "WV062",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Water Vapor",
                        "Central_Wavelength_Um": 6.25,
                        "Spectral_Interval_Um": 1.80,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    6: {
                        "Channel_no": 6,
                        "Channel_Name": "WV7.3 - Water Vapor",
                        "Channel_Band_Variable_Name": "WV073",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Water Vapor",
                        "Central_Wavelength_Um": 7.35,
                        "Spectral_Interval_Um": 1.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    7: {
                        "Channel_no": 7,
                        "Channel_Name": "IR8.7 - Infrared",
                        "Channel_Band_Variable_Name": "IR087",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 8.70,
                        "Spectral_Interval_Um": 0.80,
                        "SNR_OR_NEΔT_AT_Specified_Input": "0.17-0.28 K @ 300 K (measured range, MSG-1, Schmetz2000 Table 2)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    8: {
                        "Channel_no": 8,
                        "Channel_Name": "IR9.7 - Infrared (ozone, experimental)",
                        "Channel_Band_Variable_Name": "IR097",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 9.66,
                        "Spectral_Interval_Um": 0.56,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    9: {
                        "Channel_no": 9,
                        "Channel_Name": "IR10.8 - IR window, clean (this project's Phase-0/2 IR window, clean)",
                        "Channel_Band_Variable_Name": "IR108",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 10.80,
                        "Spectral_Interval_Um": 2.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": "0.15 K @ 300 K (measured, MSG-1; spec requirement 0.25 K @ 300 K, Schmetz2000 Table 2)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    10: {
                        "Channel_no": 10,
                        "Channel_Name": "IR12.0 - IR window, dirty (this project's Phase-2 IR window, dirty)",
                        "Channel_Band_Variable_Name": "IR120",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 12.00,
                        "Spectral_Interval_Um": 2.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": None,
                        "SNR_OR_NEΔT_Note": "Not sourced in this research pass -- previous uniform '10.1 @ 1% albedo' placeholder removed since SNR-at-albedo is the wrong metric category for this thermal-IR channel (needs NEdT-at-brightness-temperature). See Schmetz2000 Table 2.",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    11: {
                        "Channel_no": 11,
                        "Channel_Name": "IR13.4 - Infrared (CO2 absorption)",
                        "Channel_Band_Variable_Name": "IR134",
                        'Standard_Name': 'toa_brightness_temperature',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Infrared",
                        "Central_Wavelength_Um": 13.40,
                        "Spectral_Interval_Um": 2.00,
                        "SNR_OR_NEΔT_AT_Specified_Input": "0.30 K @ 270 K (measured, MSG-1; spec requirement 1.80 K @ 270 K, Schmetz2000 Table 2)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 4.8,
                            "Native_Sampling_Km": 3.0,
                            "Dataset_Resolution_Km": 3.0
                        }
                    },
                    12: {
                        "Channel_no": 12,
                        "Channel_Name": "HRV - High-Resolution Visible",
                        "Channel_Band_Variable_Name": "HRV",
                        'Standard_Name': 'toa_bidirectional_reflectance',
                        "Band_Wavelength_Variable_Name": None,
                        "Channel_Type": "Visible",
                        "Central_Wavelength_Um": 0.75,
                        "Spectral_Interval_Um": 0.30,
                        "SNR_OR_NEΔT_AT_Specified_Input": "10.1 @ 1 % albedo (VIS0.6 figure re-used as an approximation -- per-channel SNR not sourced in this research pass)",
                        "Spatial_Resolution": {
                            "Instrument_IFOV_Km": 1.6,
                            "Native_Sampling_Km": 1.0,
                            "Dataset_Resolution_Km": 1.0
                        }
                    }
            }
        }
    }
}
# =============================================================================
# Entries below added while migrating into Global_Mosaic (see project README).
# Cross-checked against space.oscar.wmo.int,
# EXCEPT mtsat-01 / mtsat-02, which are carried over from the original
# config.py SAT_DATA_AVAILABILITY/SAT_LON dicts and not independently
# re-checked -- OSCAR's slug for these two 404'd under every guess tried;
# cross-check space.oscar.wmo.int before relying on their exact dates.
#
# Correction found during this migration: OSCAR shows INSAT-3DR at 74.0E
# (not 82.0E) and INSAT-3DS at 82.0E (operational since Feb 2024). The
# project's Phase 0 config previously assumed INSAT-3DR occupied 82E --
# it doesn't; INSAT-3DS is the correct current occupant of that slot. Also,
# as of March 2023, Meteosat-10 (not -11) is Prime (0E); Meteosat-11 is the
# backup/rapid-scan satellite at 9.5E. Both corrections need to flow into
# config.py / satellites.yaml.
# =============================================================================

Satellites_Objects["meteosat-10"] = {
    "Satellite_ID": "Meteosat-10",
    "Operator": "EUMETSAT / ESA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat Second Generation (MSG)",
    "Satellite_Full_Name": "Meteosat-10",
    "Satellite_Status": "Operational",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 0.0,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 0.0},
    "Longitude_History_Note": (
        "Initially ~3.4E; became Prime (0E) 2013-01-21; moved to 9.5E for "
        "Rapid Scan Service; returned to Prime (0E) 2023-03-22, current."
    ),
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2012-12-18T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2012-12-18T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://api.eumetsat.int/data/",
    "File_Type": "Native Binary",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_10",
                         "https://space.oscar.wmo.int/instruments/view/seviri"],
    "Instruments": {
        "SEVIRI": {
            "SEVIRI_Instrument": "Spinning Enhanced Visible and Infrared Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 12,
            "Full_Disk_Temporal_Resolution_Minutes": 15,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 15,
                "Full_Disk_Scan_Duration_Minutes": 12.5,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Best: Trailer record ImageProductionStats.ActualScanningSummary.ForwardScanStart/ForwardScanEnd, averaged. Best-of-all: per-line L10LineMeanAcquisitionTime field. Filename-only approximation: filename_time - 6.25 min (filename timestamp is scan END time, checked 2026-07-30). See meteosat-09 entry for full detail.",
                "Mid_Time_Confidence": "approximate",
            },
            "Channels": "same band set as meteosat-09 (VIS006, VIS008, NIR016, WV039,"
                        " WV062, WV073, IR087, IR097, IR108, IR120, IR134, HRV) -- see"
                        " that entry for full per-channel detail.",
        }
    }
}

Satellites_Objects["meteosat-11"] = {
    "Satellite_ID": "Meteosat-11",
    "Operator": "EUMETSAT / ESA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Meteosat Second Generation (MSG)",
    "Satellite_Full_Name": "Meteosat-11",
    "Satellite_Status": "Operational (backup / Rapid Scan Service)",
    "Scanning_Mode": "Full Disk / Rapid Scan",
    "Nominal_Longitude_Deg": 9.5,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 9.5},
    "Longitude_History_Note": (
        "In storage until late 2017; moved to Prime (0E) 2018-02-20; served "
        "as Prime until 2023-03-22 when Meteosat-10 took back Prime and "
        "Meteosat-11 moved to 9.5E backup/Rapid Scan Service, current."
    ),
    "Satellite_Nominal_Lon_Variable_Name": "nominal_sub_satellite_longitude",
    "Satellite_Time_Variable_Name": "time",
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": True,
    "Operation_Start_Date": "2018-02-20T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2018-02-20T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://api.eumetsat.int/data/",
    "File_Type": "Native Binary",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/meteosat_11",
                         "https://space.oscar.wmo.int/instruments/view/seviri"],
    "Instruments": {
        "SEVIRI": {
            "SEVIRI_Instrument": "Spinning Enhanced Visible and Infrared Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 12,
            "Full_Disk_Temporal_Resolution_Minutes": 15,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 15,
                "Full_Disk_Scan_Duration_Minutes": 12.5,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Best: Trailer record ImageProductionStats.ActualScanningSummary.ForwardScanStart/ForwardScanEnd, averaged. Best-of-all: per-line L10LineMeanAcquisitionTime field. Filename-only approximation: filename_time - 6.25 min (filename timestamp is scan END time, checked 2026-07-30). See meteosat-09 entry for full detail. NOTE: this satellite additionally runs the 5-min-cadence Rapid Scan Service over the northern third of the disk when acting as the 9.5E backup -- RSS timing is not modeled separately here.",
                "Mid_Time_Confidence": "approximate",
            },
            "Channels": "same band set as meteosat-09/-10 -- see meteosat-09 entry.",
        }
    }
}

Satellites_Objects["himawari-08"] = {
    "Satellite_ID": "Himawari-8",
    "Operator": "JMA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Himawari (3rd generation)",
    "Satellite_Full_Name": "Himawari-8",
    "Satellite_Status": "Standby (primary operations handed to Himawari-9 on 2025-11-26)",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 140.7,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 140.7},
    "Satellite_Time_Variable_Name": "start_time",
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": False,
    "Operation_Start_Date": "2015-01-07T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2015-07-07T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://noaa-himawari8.s3.amazonaws.com/",
    "File_Type": "HSD (segmented, bz2)",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/himawari_8",
                         "https://space.oscar.wmo.int/instruments/view/ahi"],
    "Instruments": {
        "AHI": {
            "AHI_Instrument": "Advanced Himawari Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 16,
            "Full_Disk_Temporal_Resolution_Minutes": 10,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 10,
                "Full_Disk_Scan_Duration_Minutes": None,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Filename yyyymmdd_hhnn gives only the nominal 10-minute timeline start, to the minute -- no scan duration is published anywhere. True start/end require opening the HSD file and reading Block #1 bytes 46/54 (Observation start/end time, R8 Modified Julian Date), or Block #9 for per-scanline precision. Whether Block #1's start/end is constant or varies across a Full Disk observation's 10 segment files is not documented by JMA -- verify empirically before relying on it.",
                "Mid_Time_Confidence": "unverified",
            },
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "B01 - Blue",
                    "Channel_Band_Variable_Name": "B01",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.47,
                    "Flight_Model_Wavelength_Um": 0.47063,
                    "Measured_SRF_Peak_Um": 0.4810,
                    "Measured_SRF_FWHM_Um": 0.0407,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 1.0,
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "B02 - Green",
                    "Channel_Band_Variable_Name": "B02",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.51,
                    "Flight_Model_Wavelength_Um": 0.51000,
                    "Measured_SRF_Peak_Um": 0.5150,
                    "Measured_SRF_FWHM_Um": 0.0308,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 1.0,
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "B03 - Red / VIS (this project's Phase-2 Visible)",
                    "Channel_Band_Variable_Name": "B03",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.64,
                    "Flight_Model_Wavelength_Um": 0.63914,
                    "Measured_SRF_Peak_Um": 0.6490,
                    "Measured_SRF_FWHM_Um": 0.0817,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 0.5,
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "B04 - Vegetation/Near-Infrared",
                    "Channel_Band_Variable_Name": "B04",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 0.86,
                    "Flight_Model_Wavelength_Um": 0.85670,
                    "Measured_SRF_Peak_Um": 0.8650,
                    "Measured_SRF_FWHM_Um": 0.0345,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 1.0,
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "B05 - Snow/Ice (this project's Phase-2 Shortwave IR)",
                    "Channel_Band_Variable_Name": "B05",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 1.6,
                    "Flight_Model_Wavelength_Um": 1.6101,
                    "Measured_SRF_Peak_Um": 1.6139,
                    "Measured_SRF_FWHM_Um": 0.0409,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                6: {
                    "Channel_no": 6,
                    "Channel_Name": "B06 - Cloud particle size",
                    "Channel_Band_Variable_Name": "B06",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 2.3,
                    "Flight_Model_Wavelength_Um": 2.2568,
                    "Measured_SRF_Peak_Um": 2.2558,
                    "Measured_SRF_FWHM_Um": 0.0441,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                7: {
                    "Channel_no": 7,
                    "Channel_Name": "B07 - IR4, shortwave IR window (this project's Phase-2 Mid IR)",
                    "Channel_Band_Variable_Name": "B07",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Flight_Model_Wavelength_Um": 3.8853,
                    "Measured_SRF_Peak_Um": 3.8749,
                    "Measured_SRF_FWHM_Um": 0.2006,
                    "Valid_Bits_Per_Pixel": 14,
                    "Spatial_Resolution_Km": 2.0,
                },
                8: {
                    "Channel_no": 8,
                    "Channel_Name": "B08 - IR3, upper-level water vapor",
                    "Channel_Band_Variable_Name": "B08",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.2,
                    "Flight_Model_Wavelength_Um": 6.2429,
                    "Measured_SRF_Peak_Um": 6.4255,
                    "Measured_SRF_FWHM_Um": 0.8219,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                9: {
                    "Channel_no": 9,
                    "Channel_Name": "B09 - Mid-level water vapor (this project's Phase-2 Water vapor)",
                    "Channel_Band_Variable_Name": "B09",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.9,
                    "Flight_Model_Wavelength_Um": 6.9410,
                    "Measured_SRF_Peak_Um": 6.8790,
                    "Measured_SRF_FWHM_Um": 0.4019,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                10: {
                    "Channel_no": 10,
                    "Channel_Name": "B10 - Lower-level water vapor",
                    "Channel_Band_Variable_Name": "B10",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 7.3,
                    "Flight_Model_Wavelength_Um": 7.3467,
                    "Measured_SRF_Peak_Um": 7.3078,
                    "Measured_SRF_FWHM_Um": 0.1871,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                11: {
                    "Channel_no": 11,
                    "Channel_Name": "B11 - Cloud-top phase, SO2",
                    "Channel_Band_Variable_Name": "B11",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 8.6,
                    "Flight_Model_Wavelength_Um": 8.5926,
                    "Measured_SRF_Peak_Um": 8.6949,
                    "Measured_SRF_FWHM_Um": 0.3727,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                12: {
                    "Channel_no": 12,
                    "Channel_Name": "B12 - Total column ozone",
                    "Channel_Band_Variable_Name": "B12",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 9.6,
                    "Flight_Model_Wavelength_Um": 9.6372,
                    "Measured_SRF_Peak_Um": 9.7456,
                    "Measured_SRF_FWHM_Um": 0.3779,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                13: {
                    "Channel_no": 13,
                    "Channel_Name": "B13 - IR1, IR window (clean)",
                    "Channel_Band_Variable_Name": "B13",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 10.4,
                    "Flight_Model_Wavelength_Um": 10.4073,
                    "Measured_SRF_Peak_Um": 10.3875,
                    "Measured_SRF_FWHM_Um": 0.4189,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                14: {
                    "Channel_no": 14,
                    "Channel_Name": "B14 - IR window, SST/cloud imagery",
                    "Channel_Band_Variable_Name": "B14",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 11.2,
                    "Flight_Model_Wavelength_Um": 11.2395,
                    "Measured_SRF_Peak_Um": 11.0436,
                    "Measured_SRF_FWHM_Um": 0.6678,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                15: {
                    "Channel_no": 15,
                    "Channel_Name": "B15 - IR2, dirty IR window (this project's Phase-2 IR window, dirty)",
                    "Channel_Band_Variable_Name": "B15",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 12.4,
                    "Flight_Model_Wavelength_Um": 12.3806,
                    "Measured_SRF_Peak_Um": 12.0207,
                    "Measured_SRF_FWHM_Um": 0.9656,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                16: {
                    "Channel_no": 16,
                    "Channel_Name": "B16 - CO2 absorption, air temperature/cloud height",
                    "Channel_Band_Variable_Name": "B16",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 13.3,
                    "Flight_Model_Wavelength_Um": 13.2807,
                    "Measured_SRF_Peak_Um": 13.2118,
                    "Measured_SRF_FWHM_Um": 0.5638,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
            },
        }
    }
}

Satellites_Objects["himawari-09"] = {
    "Satellite_ID": "Himawari-9",
    "Operator": "JMA",
    "Orbit_Type": "Geostationary",
    "Footprint_Model": "Geostationary spherical Earth",
    "Satellite_Series": "Himawari (3rd generation)",
    "Satellite_Full_Name": "Himawari-9",
    "Satellite_Status": "Operational (primary, resumed 2025-11-26 after imagery anomaly)",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 140.7,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 140.7},
    "Satellite_Time_Variable_Name": "start_time",
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Sat_Lat_Lon_Change_During_Active_Operation": False,
    "Operation_Start_Date": "2017-02-10T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2016-11-02T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://noaa-himawari9.s3.amazonaws.com/",
    "File_Type": "HSD (segmented, bz2)",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/himawari_9",
                         "https://space.oscar.wmo.int/instruments/view/ahi"],
    "Instruments": {
        "AHI": {
            "AHI_Instrument": "Advanced Himawari Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 16,
            "Full_Disk_Temporal_Resolution_Minutes": 10,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 10,
                "Full_Disk_Scan_Duration_Minutes": None,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Filename yyyymmdd_hhnn gives only the nominal 10-minute timeline start, to the minute -- no scan duration is published anywhere. True start/end require opening the HSD file and reading Block #1 bytes 46/54 (Observation start/end time, R8 Modified Julian Date), or Block #9 for per-scanline precision. Whether Block #1's start/end is constant or varies across a Full Disk observation's 10 segment files is not documented by JMA -- verify empirically before relying on it.",
                "Mid_Time_Confidence": "unverified",
            },
            "Channels": {
                1: {
                    "Channel_no": 1,
                    "Channel_Name": "B01 - Blue",
                    "Channel_Band_Variable_Name": "B01",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.47,
                    "Flight_Model_Wavelength_Um": 0.47059,
                    "Measured_SRF_Peak_Um": 0.4810,
                    "Measured_SRF_FWHM_Um": 0.0408,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 1.0,
                },
                2: {
                    "Channel_no": 2,
                    "Channel_Name": "B02 - Green",
                    "Channel_Band_Variable_Name": "B02",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.51,
                    "Flight_Model_Wavelength_Um": 0.50993,
                    "Measured_SRF_Peak_Um": 0.5150,
                    "Measured_SRF_FWHM_Um": 0.0307,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 1.0,
                },
                3: {
                    "Channel_no": 3,
                    "Channel_Name": "B03 - Red / VIS (this project's Phase-2 Visible)",
                    "Channel_Band_Variable_Name": "B03",
                    "Channel_Type": "Visible",
                    "Central_Wavelength_Um": 0.64,
                    "Flight_Model_Wavelength_Um": 0.63972,
                    "Measured_SRF_Peak_Um": 0.6410,
                    "Measured_SRF_FWHM_Um": 0.0815,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 0.5,
                },
                4: {
                    "Channel_no": 4,
                    "Channel_Name": "B04 - Vegetation/Near-Infrared",
                    "Channel_Band_Variable_Name": "B04",
                    "Channel_Type": "Near-Infrared",
                    "Central_Wavelength_Um": 0.86,
                    "Flight_Model_Wavelength_Um": 0.85668,
                    "Measured_SRF_Peak_Um": 0.8650,
                    "Measured_SRF_FWHM_Um": 0.0344,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 1.0,
                },
                5: {
                    "Channel_no": 5,
                    "Channel_Name": "B05 - Snow/Ice (this project's Phase-2 Shortwave IR)",
                    "Channel_Band_Variable_Name": "B05",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 1.6,
                    "Flight_Model_Wavelength_Um": 1.6065,
                    "Measured_SRF_Peak_Um": 1.5972,
                    "Measured_SRF_FWHM_Um": 0.0450,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                6: {
                    "Channel_no": 6,
                    "Channel_Name": "B06 - Cloud particle size",
                    "Channel_Band_Variable_Name": "B06",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 2.3,
                    "Flight_Model_Wavelength_Um": 2.2570,
                    "Measured_SRF_Peak_Um": 2.2558,
                    "Measured_SRF_FWHM_Um": 0.0441,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                7: {
                    "Channel_no": 7,
                    "Channel_Name": "B07 - IR4, shortwave IR window (this project's Phase-2 Mid IR)",
                    "Channel_Band_Variable_Name": "B07",
                    "Channel_Type": "Shortwave Infrared",
                    "Central_Wavelength_Um": 3.9,
                    "Flight_Model_Wavelength_Um": 3.8289,
                    "Measured_SRF_Peak_Um": 3.8111,
                    "Measured_SRF_FWHM_Um": 0.1946,
                    "Valid_Bits_Per_Pixel": 14,
                    "Spatial_Resolution_Km": 2.0,
                },
                8: {
                    "Channel_no": 8,
                    "Channel_Name": "B08 - IR3, upper-level water vapor",
                    "Channel_Band_Variable_Name": "B08",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.2,
                    "Flight_Model_Wavelength_Um": 6.2479,
                    "Measured_SRF_Peak_Um": 6.4280,
                    "Measured_SRF_FWHM_Um": 0.8194,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                9: {
                    "Channel_no": 9,
                    "Channel_Name": "B09 - Mid-level water vapor (this project's Phase-2 Water vapor)",
                    "Channel_Band_Variable_Name": "B09",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 6.9,
                    "Flight_Model_Wavelength_Um": 6.9555,
                    "Measured_SRF_Peak_Um": 7.0240,
                    "Measured_SRF_FWHM_Um": 0.3840,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
                10: {
                    "Channel_no": 10,
                    "Channel_Name": "B10 - Lower-level water vapor",
                    "Channel_Band_Variable_Name": "B10",
                    "Channel_Type": "Water Vapor",
                    "Central_Wavelength_Um": 7.3,
                    "Flight_Model_Wavelength_Um": 7.3437,
                    "Measured_SRF_Peak_Um": 7.3687,
                    "Measured_SRF_FWHM_Um": 0.1870,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                11: {
                    "Channel_no": 11,
                    "Channel_Name": "B11 - Cloud-top phase, SO2",
                    "Channel_Band_Variable_Name": "B11",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 8.6,
                    "Flight_Model_Wavelength_Um": 8.5936,
                    "Measured_SRF_Peak_Um": 8.6513,
                    "Measured_SRF_FWHM_Um": 0.3684,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                12: {
                    "Channel_no": 12,
                    "Channel_Name": "B12 - Total column ozone",
                    "Channel_Band_Variable_Name": "B12",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 9.6,
                    "Flight_Model_Wavelength_Um": 9.6274,
                    "Measured_SRF_Peak_Um": 9.7324,
                    "Measured_SRF_FWHM_Um": 0.3751,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                13: {
                    "Channel_no": 13,
                    "Channel_Name": "B13 - IR1, IR window (clean)",
                    "Channel_Band_Variable_Name": "B13",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 10.4,
                    "Flight_Model_Wavelength_Um": 10.4074,
                    "Measured_SRF_Peak_Um": 10.3745,
                    "Measured_SRF_FWHM_Um": 0.4269,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                14: {
                    "Channel_no": 14,
                    "Channel_Name": "B14 - IR window, SST/cloud imagery",
                    "Channel_Band_Variable_Name": "B14",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 11.2,
                    "Flight_Model_Wavelength_Um": 11.2080,
                    "Measured_SRF_Peak_Um": 11.0229,
                    "Measured_SRF_FWHM_Um": 0.6558,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                15: {
                    "Channel_no": 15,
                    "Channel_Name": "B15 - IR2, dirty IR window (this project's Phase-2 IR window, dirty)",
                    "Channel_Band_Variable_Name": "B15",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 12.4,
                    "Flight_Model_Wavelength_Um": 12.3648,
                    "Measured_SRF_Peak_Um": 12.0062,
                    "Measured_SRF_FWHM_Um": 0.9587,
                    "Valid_Bits_Per_Pixel": 12,
                    "Spatial_Resolution_Km": 2.0,
                },
                16: {
                    "Channel_no": 16,
                    "Channel_Name": "B16 - CO2 absorption, air temperature/cloud height",
                    "Channel_Band_Variable_Name": "B16",
                    "Channel_Type": "Infrared",
                    "Central_Wavelength_Um": 13.3,
                    "Flight_Model_Wavelength_Um": 13.3107,
                    "Measured_SRF_Peak_Um": 13.1285,
                    "Measured_SRF_FWHM_Um": 0.5674,
                    "Valid_Bits_Per_Pixel": 11,
                    "Spatial_Resolution_Km": 2.0,
                },
            },
        }
    }
}

Satellites_Objects["himawari-10"] = {
    "Satellite_ID": "Himawari-10",
    "Operator": "JMA",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "Himawari (4th generation)",
    "Satellite_Full_Name": "Himawari-10",
    "Satellite_Status": "Planned -- NOT YET LAUNCHED",
    "Nominal_Longitude_Deg": 140.7,
    "Operation_Start_Date": None,
    "Operation_End_Date": None,
    "Data_Available": False,
    "Data_Available_Start_Date": None,
    "Date_Available_End_Date": None,
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/himawari_10"],
    "Instruments": {
        "GHMI": {"Instrument_Type": "Planned imager, no data yet (est. launch >= 2030)"}
    },
    "Note": "Placeholder only -- excluded from any Phase 0-3 acquisition until launched.",
}

Satellites_Objects["mtsat-01"] = {
    "Satellite_ID": "MTSAT-1R",
    "Operator": "JMA",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "MTSAT (a.k.a. Himawari-6)",
    "Satellite_Full_Name": "Multi-functional Transport Satellite - 1R",
    "Satellite_Status": "Retired/deactivated",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 140.0,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 140.0},
    "Operation_Start_Date": "2005-06-28T00:00:00",
    "Operation_End_Date": "2010-07-01T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2005-06-28T00:00:00",
    "Date_Available_End_Date": "2010-07-01T00:00:00",
    "Metadata_Sources": [
        "NOT independently re-checked against a live OSCAR fetch "
        "(space.oscar.wmo.int/satellites/view/mtsat_1r 404'd under "
        "every slug tried) -- carried over from prior project config.py. "
        "Cross-check before relying on exact dates."
    ],
    "Instruments": {
        "JAMI": {
            "JAMI_Instrument": "Japanese Advanced Meteorological Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {"Channel_no": 1, "Channel_Name": "IR1", "Channel_Band_Variable_Name": "IR1",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 10.8, "Spatial_Resolution_Km": 4.0},
            },
        }
    }
}

Satellites_Objects["mtsat-02"] = {
    "Satellite_ID": "MTSAT-2",
    "Operator": "JMA",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "MTSAT (a.k.a. Himawari-7)",
    "Satellite_Full_Name": "Multi-functional Transport Satellite - 2",
    "Satellite_Status": "Retired/deactivated",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 145.0,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 145.0},
    "Operation_Start_Date": "2010-07-01T00:00:00",
    "Operation_End_Date": "2015-07-07T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2010-07-01T00:00:00",
    "Date_Available_End_Date": "2015-07-07T00:00:00",
    "Metadata_Sources": [
        "NOT independently re-checked against a live OSCAR fetch "
        "(space.oscar.wmo.int/satellites/view/mtsat_2 404'd under "
        "every slug tried) -- carried over from prior project config.py. "
        "Cross-check before relying on exact dates."
    ],
    "Instruments": {
        "MTSAT-2 Imager": {
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 5,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                1: {"Channel_no": 1, "Channel_Name": "IR1", "Channel_Band_Variable_Name": "IR1",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 10.8, "Spatial_Resolution_Km": 4.0},
            },
        }
    }
}

Satellites_Objects["gk2a"] = {
    "Satellite_ID": "GK2A",
    "Operator": "KMA / KARI",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "GEO-KOMPSAT-2",
    "Satellite_Full_Name": "Geostationary Korea Multi-Purpose Satellite - 2A",
    "Satellite_Status": "Operational",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 128.2,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 128.2},
    "Operation_Start_Date": "2019-03-25T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2020-07-01T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://nmsc.kma.go.kr/ (KMA NMSC portal, account required)",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/geo_kompsat_2a"],
    "Instruments": {
        "AMI": {
            "AMI_Instrument": "Advanced Meteorological Imager",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 16,
            "Full_Disk_Temporal_Resolution_Minutes": 10,
            "Channels": {
                13: {"Channel_no": 13, "Channel_Name": "IR105 - IR window (clean)",
                     "Channel_Band_Variable_Name": "IR105", "Channel_Type": "Infrared",
                     "Central_Wavelength_Um": 10.5, "Spatial_Resolution_Km": 2.0},
            },
        }
    }
}

Satellites_Objects["gk2b"] = {
    "Satellite_ID": "GK2B",
    "Operator": "KMA / KARI / NIER / MLTM",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "GEO-KOMPSAT-2",
    "Satellite_Full_Name": "Geostationary Korea Multi-Purpose Satellite - 2B",
    "Satellite_Status": "Operational",
    "Nominal_Longitude_Deg": 128.2,
    "Operation_Start_Date": "2020-05-01T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/geo_kompsat_2b"],
    "Instruments": {
        "GOCI-II": {"Instrument_Type": "Regional ocean-color imager, not full-disk meteorological"},
        "GEMS": {"Instrument_Type": "Atmospheric chemistry spectrometer, not full-disk meteorological"},
    },
    "Note": (
        "NOT suitable for the global IR-window mosaic -- carries no full-disk "
        "meteorological imager (GOCI-II is a regional ocean-color instrument, "
        "GEMS is an atmospheric-chemistry spectrometer). Included for "
        "completeness only; excluded from acquisition/reader_registry."
    ),
}

Satellites_Objects["kalpana-1"] = {
    "Satellite_ID": "Kalpana-1",
    "Operator": "ISRO / IMD",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "INSAT (Kalpana)",
    "Satellite_Full_Name": "Kalpana-1",
    "Satellite_Status": "Inactive",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 74.0,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 74.0},
    "Operation_Start_Date": "2002-12-01T00:00:00",
    "Operation_End_Date": "2017-09-01T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2002-09-12T00:00:00",
    "Date_Available_End_Date": "2017-09-27T00:00:00",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/kalpana_1"],
    "Instruments": {
        "VHRR": {
            "VHRR_Instrument": "Very High Resolution Radiometer",
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 3,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Channels": {
                3: {"Channel_no": 3, "Channel_Name": "TIR", "Channel_Band_Variable_Name": "TIR",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 11.0, "Spatial_Resolution_Km": 8.0},
            },
        }
    }
}

Satellites_Objects["insat-3d"] = {
    "Satellite_ID": "INSAT-3D",
    "Operator": "ISRO / IMD",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "INSAT-3D",
    "Satellite_Full_Name": "Indian National Satellite - 3D",
    "Satellite_Status": "Backup (sounder failed Sept 2020)",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 129.5,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 129.5},
    "Longitude_History_Note": "Relocated to 129.5E backup slot; not at 82E despite that being its originally-assumed slot.",
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Altitude_Note": "Standard WGS84/geostationary reference values, matching every other satellite in this file -- added 2026-07-31 (all 3 INSAT entries previously had no altitude/Earth-radius fields at all, silently forcing any geometry code to fall back to hardcoded defaults instead of reading real values). A real INSAT-3DS L1B file's own Nominal_Altitude(km) root attribute reads 36000.0 -- a rounder design figure, ~214 km higher than the WGS84-precise value used here; kept consistent with the rest of this file rather than switching to the file's own rounder number.",
    "Operation_Start_Date": "2013-10-15T00:00:00",
    "Operation_End_Date": "2024-10-01T00:00:00",
    "Data_Available": True,
    "Data_Available_Start_Date": "2013-07-26T00:00:00",
    "Date_Available_End_Date": None,
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/insat_3d"],
    "Instruments": {
        "Imager": {
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 6,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 30,
                "Full_Disk_Scan_Duration_Minutes": 25.5,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Open the L1B/L1C HDF5 file and read root attributes Acquisition_Start_Time / Acquisition_End_Time (format DD-MM-YYYYTHH:MM:SS), average them. The filename's HHmm field (= Acquisition_Time_in_GMT) is only a 'representative' time -- its exact arithmetic relationship to the true start/end/mid is not documented by any source reviewed. Previously this field was a single ambiguous '26' (minutes) conflating repeat cadence with scan duration -- corrected 2026-07-30: 30 min is the repeat cadence (48 scans/day), ~25-26 min is the scan duration within that slot.",
                "Mid_Time_Confidence": "unverified",
            },
            "Channels": {
                1: {"Channel_no": 1, "Channel_Name": "VIS", "Channel_Band_Variable_Name": "IMG_VIS",
                    "Channel_Type": "Visible", "Central_Wavelength_Um": 0.65, "Spatial_Resolution_Km": 1.0},
                2: {"Channel_no": 2, "Channel_Name": "SWIR", "Channel_Band_Variable_Name": "IMG_SWIR",
                    "Channel_Type": "Shortwave Infrared", "Central_Wavelength_Um": 1.625, "Spatial_Resolution_Km": 1.0},
                3: {"Channel_no": 3, "Channel_Name": "MIR", "Channel_Band_Variable_Name": "IMG_MIR",
                    "Channel_Type": "Shortwave Infrared", "Central_Wavelength_Um": 3.82, "Spatial_Resolution_Km": 4.0},
                4: {"Channel_no": 4, "Channel_Name": "WV", "Channel_Band_Variable_Name": "IMG_WV",
                    "Channel_Type": "Water Vapor", "Central_Wavelength_Um": 6.8, "Spatial_Resolution_Km": 8.0},
                5: {"Channel_no": 5, "Channel_Name": "TIR1", "Channel_Band_Variable_Name": "IMG_TIR1",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 10.8, "Spatial_Resolution_Km": 4.0},
                6: {"Channel_no": 6, "Channel_Name": "TIR2", "Channel_Band_Variable_Name": "IMG_TIR2",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 12.0, "Spatial_Resolution_Km": 4.0},
            },
        },
        "Sounder": {"Instrument_Type": "19-channel sounder, FAILED Sept 2020"},
    }
}

Satellites_Objects["insat-3dr"] = {
    "Satellite_ID": "INSAT-3DR",
    "Operator": "ISRO / IMD",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "INSAT-3D",
    "Satellite_Full_Name": "Indian National Satellite - 3D Repeat",
    "Satellite_Status": "Operational",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 74.0,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 74.0},
    "Longitude_History_Note": "OSCAR confirms 74.0E, NOT 82.0E -- correct the earlier Phase 0 assumption.",
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Altitude_Note": "Standard WGS84/geostationary reference values, matching every other satellite in this file -- added 2026-07-31 (all 3 INSAT entries previously had no altitude/Earth-radius fields at all, silently forcing any geometry code to fall back to hardcoded defaults instead of reading real values). A real INSAT-3DS L1B file's own Nominal_Altitude(km) root attribute reads 36000.0 -- a rounder design figure, ~214 km higher than the WGS84-precise value used here; kept consistent with the rest of this file rather than switching to the file's own rounder number.",
    "Operation_Start_Date": "2016-09-08T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2016-09-08T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://mosdac.gov.in/ (account required, no public API)",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/insat_3dr"],
    "Instruments": {
        "Imager": {
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 6,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 30,
                "Full_Disk_Scan_Duration_Minutes": 25.5,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Open the L1B/L1C HDF5 file and read root attributes Acquisition_Start_Time / Acquisition_End_Time (format DD-MM-YYYYTHH:MM:SS), average them. The filename's HHmm field (= Acquisition_Time_in_GMT) is only a 'representative' time -- its exact arithmetic relationship to the true start/end/mid is not documented by any source reviewed. Previously this field was a single ambiguous '26' (minutes) conflating repeat cadence with scan duration -- corrected 2026-07-30: 30 min is the repeat cadence (48 scans/day), ~25-26 min is the scan duration within that slot.",
                "Mid_Time_Confidence": "unverified",
            },
            "Channels": {
                1: {"Channel_no": 1, "Channel_Name": "VIS", "Channel_Band_Variable_Name": "IMG_VIS",
                    "Channel_Type": "Visible", "Central_Wavelength_Um": 0.65, "Spatial_Resolution_Km": 1.0},
                2: {"Channel_no": 2, "Channel_Name": "SWIR", "Channel_Band_Variable_Name": "IMG_SWIR",
                    "Channel_Type": "Shortwave Infrared", "Central_Wavelength_Um": 1.625, "Spatial_Resolution_Km": 1.0},
                3: {"Channel_no": 3, "Channel_Name": "MIR", "Channel_Band_Variable_Name": "IMG_MIR",
                    "Channel_Type": "Shortwave Infrared", "Central_Wavelength_Um": 3.82, "Spatial_Resolution_Km": 4.0},
                4: {"Channel_no": 4, "Channel_Name": "WV", "Channel_Band_Variable_Name": "IMG_WV",
                    "Channel_Type": "Water Vapor", "Central_Wavelength_Um": 6.8, "Spatial_Resolution_Km": 8.0},
                5: {"Channel_no": 5, "Channel_Name": "TIR1", "Channel_Band_Variable_Name": "IMG_TIR1",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 10.8, "Spatial_Resolution_Km": 4.0},
                6: {"Channel_no": 6, "Channel_Name": "TIR2", "Channel_Band_Variable_Name": "IMG_TIR2",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 12.0, "Spatial_Resolution_Km": 4.0},
            },
        }
    }
}

Satellites_Objects["insat-3ds"] = {
    "Satellite_ID": "INSAT-3DS",
    "Operator": "ISRO / IMD",
    "Orbit_Type": "Geostationary",
    "Satellite_Series": "INSAT-3D",
    "Satellite_Full_Name": "Indian National Satellite - 3D Second Repeat",
    "Satellite_Status": "Operational",
    "Scanning_Mode": "Full Disk",
    "Nominal_Longitude_Deg": 82.0,
    "Sub_Satellite_Point": {"lat": 0.0, "lon": 82.0},
    "Longitude_History_Note": "OSCAR confirms this (not INSAT-3DR) is the current occupant of the 82.0E slot.",
    "Altitude_Km": 35786.226,
    "Earth_Radius_Km": 6378.137,
    "Orbit_Radius_Km": 42164.363,
    "Max_Viewing_Angle_Deg": 81.3,
    "Max_Visible_Latitude_Deg": 66.5,
    "Altitude_Note": "Standard WGS84/geostationary reference values, matching every other satellite in this file -- added 2026-07-31 (all 3 INSAT entries previously had no altitude/Earth-radius fields at all, silently forcing any geometry code to fall back to hardcoded defaults instead of reading real values). A real INSAT-3DS L1B file's own Nominal_Altitude(km) root attribute reads 36000.0 -- a rounder design figure, ~214 km higher than the WGS84-precise value used here; kept consistent with the rest of this file rather than switching to the file's own rounder number.",
    "Operation_Start_Date": "2024-02-17T00:00:00",
    "Operation_End_Date": None,
    "Data_Available": True,
    "Data_Available_Start_Date": "2024-02-17T00:00:00",
    "Date_Available_End_Date": None,
    "Data_Dissemination_From": "https://mosdac.gov.in/ (account required, no public API)",
    "Metadata_Sources": ["https://space.oscar.wmo.int/satellites/view/insat_3ds"],
    "Instruments": {
        "Imager": {
            "Instrument_Type": "Multispectral Imager",
            "Total_Channels": 6,
            "Full_Disk_Temporal_Resolution_Minutes": 30,
            "Scan_Timing": {
                "Full_Disk_Repeat_Cadence_Minutes": 30,
                "Full_Disk_Scan_Duration_Minutes": 25.5,
                "Mid_Time_Source": "file_header",
                "Mid_Time_Method": "Open the L1B/L1C HDF5 file and read root attributes Acquisition_Start_Time / Acquisition_End_Time (format DD-MM-YYYYTHH:MM:SS), average them. The filename's HHmm field (= Acquisition_Time_in_GMT) is only a 'representative' time -- its exact arithmetic relationship to the true start/end/mid is not documented by any source reviewed. Previously this field was a single ambiguous '26' (minutes) conflating repeat cadence with scan duration -- corrected 2026-07-30: 30 min is the repeat cadence (48 scans/day), ~25-26 min is the scan duration within that slot.",
                "Mid_Time_Confidence": "unverified",
            },
            "Channels": {
                1: {"Channel_no": 1, "Channel_Name": "VIS", "Channel_Band_Variable_Name": "IMG_VIS",
                    "Channel_Type": "Visible", "Central_Wavelength_Um": 0.65, "Spatial_Resolution_Km": 1.0},
                2: {"Channel_no": 2, "Channel_Name": "SWIR", "Channel_Band_Variable_Name": "IMG_SWIR",
                    "Channel_Type": "Shortwave Infrared", "Central_Wavelength_Um": 1.625, "Spatial_Resolution_Km": 1.0},
                3: {"Channel_no": 3, "Channel_Name": "MIR", "Channel_Band_Variable_Name": "IMG_MIR",
                    "Channel_Type": "Shortwave Infrared", "Central_Wavelength_Um": 3.82, "Spatial_Resolution_Km": 4.0},
                4: {"Channel_no": 4, "Channel_Name": "WV", "Channel_Band_Variable_Name": "IMG_WV",
                    "Channel_Type": "Water Vapor", "Central_Wavelength_Um": 6.8, "Spatial_Resolution_Km": 4.0,
                    "Resolution_Change_Note": "INSAT-3DS upgraded the WV channel from 8 km (INSAT-3D/3DR) to 4 km -- stated verbatim in INSAT-3DS_Operational_Products_V1.pdf p.5. No other channel's resolution/wavelength changed for this generation."},
                5: {"Channel_no": 5, "Channel_Name": "TIR1", "Channel_Band_Variable_Name": "IMG_TIR1",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 10.8, "Spatial_Resolution_Km": 4.0},
                6: {"Channel_no": 6, "Channel_Name": "TIR2", "Channel_Band_Variable_Name": "IMG_TIR2",
                    "Channel_Type": "Infrared", "Central_Wavelength_Um": 12.0, "Spatial_Resolution_Km": 4.0},
            },
        }
    }
}


def get_satellite(satellite_key: str) -> dict:
    """Look up a satellite's metadata dict by its Satellites_Objects key.

    Args:
        satellite_key: Satellite key, e.g. ``"goes-16"``.

    Returns:
        The satellite's metadata dict (see the per-satellite dict schema
        documented above ``Satellites_Objects``).

    Raises:
        KeyError: If `satellite_key` is not in ``Satellites_Objects``.
    """
    return Satellites_Objects[satellite_key]


def satellites_covering(target_iso: str) -> list[str]:
    """Find all satellites whose Data_Available window covers a given time.

    Args:
        target_iso: ISO-8601 datetime string to check coverage for.

    Returns:
        Keys of all satellites in ``Satellites_Objects`` whose
        ``Data_Available_Start_Date``..``Date_Available_End_Date`` window
        covers `target_iso` (satellites with no start date are skipped;
        no end date means still-available).
    """
    import datetime as _dt

    target = _dt.datetime.fromisoformat(target_iso)
    covering = []
    for key, sat in Satellites_Objects.items():
        start = sat.get("Data_Available_Start_Date")
        end = sat.get("Date_Available_End_Date")
        if not start:
            continue
        start_dt = _dt.datetime.fromisoformat(start)
        end_dt = _dt.datetime.fromisoformat(end) if end else None
        if start_dt <= target and (end_dt is None or end_dt >= target):
            covering.append(key)
    return covering
