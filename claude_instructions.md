I want to build all the files for an integrated github repository complete with github actions and github pages. The purpose of the project is to process raw csv data from a light airplane engine monitor and visualize the data in an interactive web UI. I manage two airplanes (C-GJYY and C-FHTI) so there should be a page for both and a simple way to navigate between the two of them.

I want a raw data folder where I can drop all of my raw CSV files like the ones listed in ./raw_data/CGJYY/Flt0916_20260427F.csv. I have populated this folder with real sample data.

# Github Action
I want a github action that will process any new files I add to the ./raw_data/ folder and populate a 'processed_data' folder. 
The workflow to trigger the github action is as follows:
1. User creates a new branch and populates the raw_data folder with the new logs. 
2. User creates a PR and merges the logs into the main branch which triggers the action to populate the processed_data folder. 

The github action should scan each file and do three main things:
1. The action remove any short engine log files where the airplane did not actually go flying. This can easily be checked with a few criteria such as the max RPM for the log file (at least 2000 RMP) and/or a minimum flight duration (at least 20 mins for an engine runup )
2. Sometimes data is logged long after a flight has ended which makes the visualizations hard to reach. The action should crop each log to the good data only. 
3. Sometimes data from multiple flights gets split into separate logs. The action should automatically scan for logs without a header and, if they represent the uninterrupted continuation of a previous log, should concatenate the logs. 

# Github Pages
With all of the processed data I would like to generate a github page that visualizes the data in beautiful, modern, interactive plots. Consider using plotly. The feature list is:
1. An upper Pane showing engine data such as CHTs EGTs, RPM, Fuel Flow on the Y-axim plotted against time on the x-axis
2. A middle pane showing electrical system data such as AMPs/Volts
3. A lower pane showing data pertaining to the fuel system such RPM, fuel flow, fuel levels etc. 
4. A window with multiple rows where each row represents one engine monitor log from one flight
5. Simple navigation between aircraft (C-FHTI/C-GJYY)

# Experimental Feature 
I'm not sure if this is possible but if positional data, such as publicly available ADSB data can be pulled from somewhere on the web, like flightaware, or any other publicly accessible ADBS endpoint, I'd like to pull the positional data and display a webmap and height profile somewere alongside the engine monitor data. The plots should be linked in time so that the user can see where the aircraft was physically located for any given time steps of the engine monitor data. 