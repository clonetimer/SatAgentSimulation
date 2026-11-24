"""Compare native atmospheric densities for identical fixed geocentric positions."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import Basilisk
from Basilisk.architecture import messaging
from Basilisk.simulation import exponentialAtmosphere, msisAtmosphere, spaceWeatherData
from Basilisk.utilities import SimulationBaseClass, macros
from sat_sim.orbit.basilisk_hf import BasiliskExponentialAtmosphereConfig
from sat_sim.orbit.medium import EARTH_RADIUS_M


def run(weather_file):
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("density")
    process.addTask(sim.CreateNewTask("fixed", macros.sec2nano(1)))
    utc = messaging.EpochMsgPayload()
    utc.year, utc.month, utc.day, utc.hours, utc.minutes, utc.seconds = 2026, 3, 4, 12, 0, 0.0
    epoch = messaging.EpochMsg().write(utc)
    weather = spaceWeatherData.SpaceWeatherData()
    weather.loadSpaceWeatherFile(str(weather_file.resolve()))
    weather.epochInMsg.subscribeTo(epoch)
    earth = messaging.SpicePlanetStateMsgPayload()
    earth.J20002Pfix = [[1,0,0],[0,1,0],[0,0,1]]
    planet = messaging.SpicePlanetStateMsg().write(earth)
    msis = msisAtmosphere.MsisAtmosphere()
    msis.planetPosInMsg.subscribeTo(planet)
    msis.epochInMsg.subscribeTo(epoch)
    for i in range(23):
        msis.swDataInMsgs[i].subscribeTo(weather.swDataOutMsgs[i])
    cfg = BasiliskExponentialAtmosphereConfig()
    expo = exponentialAtmosphere.ExponentialAtmosphere()
    expo.planetRadius = EARTH_RADIUS_M
    expo.baseDensity = cfg.base_density_kg_m3
    expo.scaleHeight = cfg.scale_height_m
    expo.localTemp = cfg.local_temp_k
    expo.envMinReach = cfg.min_reach_m
    expo.envMaxReach = cfg.max_reach_m
    expo.planetPosInMsg.subscribeTo(planet)
    states = []
    cases = []
    for h in (300, 450, 600):
        for lat, lon in ((0,0), (0,180), (60,90), (-60,-90)):
            phi, lam = map(math.radians, (lat,lon))
            radius = EARTH_RADIUS_M + h*1000
            state = messaging.SCStatesMsgPayload()
            state.r_BN_N = [radius*math.cos(phi)*math.cos(lam), radius*math.cos(phi)*math.sin(lam),
                            radius*math.sin(phi)]
            msg = messaging.SCStatesMsg().write(state)
            states.append(msg)
            for model in (expo, msis):
                model.addSpacecraftToModel(msg)
            cases.append(dict(height_km=h,geocentric_lat_deg=lat,lon_deg=lon))
    sim.AddModelToTask("fixed", weather, 10)
    sim.AddModelToTask("fixed", msis, 5)
    sim.AddModelToTask("fixed", expo, 4)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(0)
    sim.ExecuteSimulation()
    for index, row in enumerate(cases):
        exponential = expo.envOutMsgs[index].read().neutralDensity
        nrl = msis.envOutMsgs[index].read().neutralDensity
        expected = cfg.reference_density_kg_m3 * math.exp((cfg.reference_altitude_m - row["height_km"]*1000)/cfg.scale_height_m)
        assert math.isclose(exponential, expected, rel_tol=1e-10, abs_tol=0)
        assert nrl > 0 and math.isfinite(nrl)
        row.update(exponential_kg_m3=exponential, msis_kg_m3=nrl, msis_to_exponential=nrl/exponential)
    return dict(epoch_utc="2026-03-04T12:00:00Z", basilisk=Basilisk.__version__,
                weather_sha256=hashlib.sha256(weather_file.read_bytes()).hexdigest(),
                reference_radius_m=EARTH_RADIUS_M, exponential=cfg.to_dict(), cases=cases)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("weather_csv", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    args.output.write_text(json.dumps(run(args.weather_csv),indent=2)+"\n",encoding="utf8")
