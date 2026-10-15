Detailed user guide
====================

Nomenclature
-------------
* Zone | A higher spatial level, typically countries
* Region | A lower spatial level, for example NUTS2 or NUTS3 regions. 
* .gms files | .gms files are the primary file format used in GAMS. They contain the modeling code written in the GAMS language. A .gms file typically includes the model definition, sets, parameters, variables, equations, objective functions, and any additional GAMS-specific syntax required to structure the model.
* .dd files | .dd files contain data input which is used in the model. 

Abbreviations
--------------
* Res | Reservoir 
* RoR | Run-off-River
* UC | Unit commitment
* WF | Workflow

highRES-Europe consists of two modules, a GAMS module and a workflow (WF) module.  

.. _workflow-label:

Workflow
------------
Work-in-progress

highRES Workflow is implemented using `Snakemake <https://snakemake.readthedocs.io/en/stable/>`_, a workflow management system that facilitates the modeling and simulation process by breaking it down into modular steps, known as rules. This ensures reproducibility and ease of use throughout the modeling process.
highRES workflow comprises a series of rules from setting up scenarios, generating, formating and preapring the input data to use with the :ref:`GAMS <GAMS-label>`, and finally converting and storing the results in different formats for subsequent analysis.

This section provides a brief overview of the highRES workflow, offering insights into its structure and highlighting its key components.       

.. _setup-config-label:

Setting configuration file 
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The highRES model relies on a configuration file named ``config_ci.yaml`` to define parameters and paths that guide the highRES workflow execution. This configuration file is the essential starting point for customizing how the model operates based on the specific needs and scenarios being analyzed. The configuration file starts with target named ``results.db.compressed``, which is the final destination for saving results based on the scenario-based paths that are defined in ``rule all`` of work flow.

Next are the ``paths`` that users need to customize based on the locations (whether operating locally or on a remote server) of the installed GAMS software ``GAMS`` (`GAMS documentation <https://www.gams.com/latest/docs/>`_), ``shared_input`` downloaded from Zenodo (See :ref:`Getting Started <getting-started-label>`) which later will be used in the workflow, ``abs_shared_code``, path containing the core model written in ``GAMS`` (see :ref:`GAMS <GAMS-label>`), and ``results`` path where user wants to save the results. Users should set these paths based on their working environement structure.   

Following the paths, the configuration file introduces a range of parameters that users can comment/uncomment based on project goals. Additional parameters can be defined, added or removed depending on specific project requirements. Below is a breif explaination of these parameters:

| **mode**
| This parameter sets the mode of operation. Choose ``developer`` mode if someone want to save all intermediate processing files (referred to as temp files in the workflow), or ``normal`` mode if only result output files are needed. 

| **years**
| Specify the weather year for input data. Someone can select from the available default data (2010) downloaded from Zenodo or substitute with their own weather data.

| **spatials**
| This parameter defines the spatial resolution for the model run. Currently, highRES models all of Europe at the region and NUTS2 levels, with ongoing development for grid-level resolution across Europe.

Following up are the Boolean parameters for wind bias correction ``bias_correction`` mainly used in ``rule build_vre_cf_grid`` and land exclusions ``elevation_excl`` based on the defined elevations for onshore wind installations. See ``rule build_vre_land_avail``.

Next parameters are refering to the solar, onshore and offshore wind capacity factors cutoff values (``cutoff-solar``, ``cutoff-onwind``, ``cutoff-offwind``) used in the ``rule build_vre_land_avail`` for land exclusions (See :ref:`Building shape files and available land <Build-shape-land-label>`). ``Aggregated_regions`` refers to the list of regions highRES can simulate and user can comment/uncomment specific regions as needed.     

``solar_clc`` and ``onwind_clc_no_buffer`` refer to the Corine Land Cover (CLC) codes that user can adjust to exclude or include the specific land types to be considered by highRES for installing new capacities. ``onwind_clc_buffer`` allows the highRES to add buffer around the given land types for new installations. These Corine code preferences are not strict and users may change considering the specific requirements. Further details are given in the subsequent sub-sections.

``co2target``  sets the emission intensity (see :ref:`GAMS <GAMS-label>` Miscellaneous equations). Finally ``cplex_options`` refers to the specific parameters for the CPLEX solver to tailor the solver behavior for optimal performance.   

At the start of the Snakemake workflow, the configuration file is loaded using the configfile directive:

::

   configfile: "config/config_ci.yaml"

.. _scenarios-setup-label:

Scenarios setup
~~~~~~~~~~~~~~~

After :ref:`Setting configuration file <setup-config-label>`, the workflow proceeds by importing various options from this configuration file. As observed in the initial lines of the workflow, different parameters are defined: ``user_mode``, ``inputyears``, ``aggregated_regions``, ``spatials``, ``cutoff_values``, ``corine_codes``, ``bias_correction``, and ``elevation_excl``. A ``date_range`` is established within the workflow to define temporal bounds, allowing highRES to operate over a specified timeframe, ranging from a few days to an entire year.

Based on the selections made in :ref:`Setting configuration file <setup-config-label>`, a set of scenarios is constructed. These scenarios combine all the defined dimensions and organize it in ``scenarios.csv``.

Subsequently various absolute and relative paths are defined to organize the model inputs, intermediate processing files, log files, accessing the input data, and storing the results. These path definitions leverage the paths defined in :ref:`Setting configuration file <setup-config-label>` file to ensure uniformity and coherence throughout the workflow. Users are encouraged to review the additional comments/details provided above each path definition in the workflow for enhanced understanding.

Generally, it is a good practice in snakemake to create a directed acyclic graph (DAG) of a workflow to visualize the jobs/rules dependencies. The DAG serves as a flow chart where each rule is represented by a node, connected by solid or dashed lines to depict dependencies. See `Snakemake documentation <https://snakemake.readthedocs.io/en/stable/tutorial/basics.html#step-4-indexing-read-alignments-and-visualizing-the-dag-of-jobs>`_. 

To generate a DAG visualization, use the following command:

::

   snakemake --dag | dot -Tpng > dag.png


.. _Build-technoeconomic-label:

Building technoeconomic inputs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The process of building technoeconomic inputs is a crucial step in the highRES workflow, involving the preparation and transformation of various data sources into model-ready formats. This mainly occurs in ``rule build_technoeconomic_inputs``. This rule integrates diverse data inputs to produce structured files necessary for subsequent modeling steps. Input data files used in this rule are either downloaded from Zenodo (See :ref:`Getting Started <getting-started-label>`) or created within the workflow such as  ``zones.csv``, ``europe_countries.csv``. Someone can incorporate their own technoeconomic data (i.e., costs, efficiencies) by adhering to the file format of ``highres_technoeconomic_database.ods``. 
This input data is processed using the transformation scripts ``data2dd_funcs.py`` and ``data2dd.py`` which includes functions for data transformation. The rule generates various ``.dd`` files in the model path, storing temporal, generation, storage, and transmission data for different scenarios. See the :ref:`GAMS <GAMS-label>` section to learn how this generated technoeconomic input data is included/used in ``highRES GAMS``.  

Following the technoeconomic data build, ``rule rename_demand_file`` tidies up demand file naming by copying the output to a new filename format ensuring consistency. `Snakemake <https://snakemake.readthedocs.io/en/v5.6.0/executable.html>`_ allows users to execute specific rules. For instance, following command will run the technoeconomic rule only:

::

   snakemake -j1 -R build_technoeconomic_inputs

.. _Build-shape-land-label:

Building shape files and available land
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``rule build_shapes`` and ``rule build_vre_land_avail`` are designed to process and prepare the regions and available lands based on the user-defined parameters in the :ref:`Setting configuration file <setup-config-label>` and other input files discussed here below.   

| **Building shape files**
| The ``rule build_shapes`` extracts and processes geographic data to produce shapefiles tailored to regions specified by the ``aggregated_regions`` in :ref:`Setting configuration file <setup-config-label>`. This rule process both onshore and offshore shape files, saving them as ``intermediate_data`` for use in subsequent rules. These operations are executed within the Jupyter notebook ``highRES-build_shapes.ipynb``.
`Snakemake <https://snakemake.readthedocs.io/en/stable/tutorial/interaction_visualization_reporting/tutorial.html>`_  supports the interactive engagement with notebooks, allowing users to edit, run, and understand processes by opening them in a browser.    
To run the ``highRES-build_shapes.ipynb`` notebook interactively, use the following command:

::

   snakemake --rerun-incomplete --edit-notebook intermediate_data/region/shapes/europe_onshore.geojson

| **Building land availability**
| Following the creation of shapefiles, ``rule build_vre_land_avail`` assesses land availability based on exclusion criteria and spatial parameters adjusted/selected in :ref:`Setting configuration file <setup-config-label>`. This rule process the land exclusions using parameters specified under ``Params`` and the data about the World Database on Protected Areas (WDPA), Corine Land Cover, and elevation and slope of the areas. Further details about these input files and their data sources are available on `Zenodo <https://zenodo.org/records/14223618>`_ .
The outputs of ``rule build_vre_land_avail`` are ``TIFF`` and ``CSV`` files, which provide spatial details on areas available for new solar, onshore, and offshore wind capacity installations. 
The detailed exclusion processes are documented in the ``highRES-build_vre_land_avail.ipynb`` notebook, which provides a comprehensive guide to data handling and transformations. Users can interactively open, edit, and explore the notebook to understand the processes better and review added comments/details for enhanced clarity.
As this rule can be computationally demanding, users should adjust the ``resources`` parameters appropriately, either running locally or on a remote server. See `Snakemake documentation <https://snakemake.readthedocs.io/en/stable/snakefiles/rules.html>`_.

To run the ``highRES-build_vre_land_avail.ipynb`` notebook interactively, use the following command:

::

   snakemake --rerun-incomplete --edit-notebook <modelpath>/grid_areas.csv

.. _Build-CF-hydro-label:

Building capacity factors and hydro inflows
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

| **Building solar and wind capacity factors**
| Converting weather data (such as solar irradiance, wind speed, runoff) into usable power system data is essential for accurately simulating renewable energy potentials. highRES uses the python-based `atlite <https://github.com/PyPSA/atlite>`_ library, which is specifically designed for this purpose. The ``rule build_vre_cf_grid`` process the two tasks.
First, it performs the wind speed bias correction using the bias correction ratios dataset (if param ``bias_correction`` is True) and then calculate the capacity factors for solar, onshore, and offshore wind at grid cell level resolution. Someone may open the ``highRES-build_vre_cf_grid.ipynb`` notebook interactively and read the additional annotations provided with code-blocks to understand or customize the code.       
Capacity factors for specified region and year are stored in a ``netcdf`` file for the use in subsequent rules.

| **Refining capacity factors**
| The ``rule build_vre_cf_inputs`` further process the capacity factors generated in ``rule build_vre_cf_grid`` and store it as ``csv`` file. It also calculates the maximum buildable power capacity of technologies, based on the ``spatial`` parameter and available areas calculated in :ref:`Building shape files and available land <Build-shape-land-label>` (see the description of ``gen.dd`` file in :ref:`Module for data input  <data-input-label>`).     
It also manage the temporal aspect of generated input data according to the parameter ``date_range`` (e.g., simulating over weeks or months). This process is documented with detail in ``highRES-build_vre_cf_inputs.ipynb`` where someone can interactively acces for refining the outputs based on specific project needs.

| **Building hydro input data**
| The ``rule build_hydro_capfac`` calculates capacity factors for hydroelectric generation by leveraging historic generation data and weather data alongside geographical inputs coming from ``rule build_shapes``. Users can delve into the input hydro data CSV files to gain a deeper understanding. ``rule build_hydro_capfac`` separates the hydro data into runoffriver and hydro reservoir parts. Runoffriver is converted into the capacity factors similar to solar/wind capacity factors while hydro reservoir data is compiled into hourly available inflows (in energy units) used as energy input to reservoir storage in the highRES ``GAMS`` model.        
The step-by-step coding process is documented in ``highRES_build_hydro.py.ipynb`` notebook. The snakemake interactive resources allows users to explore details alongside additional commentary provided in the notebook for enhanced understanding.  
The :ref:`Module for reservoir hydropower <reservoir-hydropower-label>` discussed with detail the application of this processed hydro input data with hydropower balance equations and hydro storage constraints modeled in GAMS code.   

Finally, ``rule build_vre_areas_file`` concatenates files of maximum buildable technology capacities (in megawatts) in the specified zones and regions, setting upper limits for technology capacity decision variables in GAMS (see :ref:`Module for data input <data-input-label>`).     

..
   _ #TODO: Add flow chart figure: rule build_vre_cf_grid-->rule build_vre_cf_inputs-->rule build_hydro_capfac-->rule build_vre_areas_file

.. _Run-GAMS-model-label:

Building input files and runing GAMS model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

| **Preparing input data for Modeling**
| In this formatting phase, several Snakemake rules transform the generated input data files into formats suitable for direct use with the the highRES ``GAMS`` model. 
The ``rule build_hydrores_inflow`` transforms the inflow CSV data from ``rule build_hydro_capfac`` into a ``GDX`` file format using the GAMS tool ``csv2gdx``.
The, ``rule link_hydrores_inflow`` ensures cross-platform compatibility by copying the compressed ``GDX`` inflow file, addressing potential issues on systems like Windows.
The ``rule build_vre_file`` concatenates solar, wind, and hydro run-of-river capacity factor CSVs into a single VRE generation file.
After going through different data type transformations, the ``rule build_inputs`` ensures that all necessary data files to run the ``GAMS`` model are present at the required path in appropriate format. 

| **Run GAMS**
| The ``rule run_gams`` marks the culmination of the highRES workflow, orchestrating the execution of the ``GAMS`` model to simulate designed scenarios. This step integrates all previously prepared data inputs and :ref:`GAMS <GAMS-label>` model configurations to produce results.
Parameters used within this rule are defined in :ref:`Setting configuration file <setup-config-label>`. The primary output ``results.gdx`` contains the simulation results for subsequent analysis.  
Users can refer to the log files to investigate detailed execution steps, for troubleshooting purposes and understanding the modeling processes in detail.
The execution of the GAMS script is handled by ``run_gams.py`` which sets the code folder and models paths, and captures GAMS output during execution in real-time to stream it to the terminal. 

Upon the complete execution of ``GAMS`` model, the ``result.gdx`` file is further processed in subsequent rules helpful in doing results analysis. 


.. _GAMS-label:

GAMS
------------

The general algebraic modeling system (GAMS) is the modelling system for optimisation which highRES is written in. The main GAMS file of highRES is ``highres.gms``. Here, the essential variables and equations are declared and defined. 

* The **objective equation** details the total system cost of the model, which is to be minimised. This includes capital expenditures, fixed operation and maintenance and variable operation and maintenance for generation, storage and transmission infrastructure. 
* The **demand balance equation** ensures that the supply ≥ demand for every hour in every zone. 
* The **transmission equations** allows for electricity to flow between zones for every hour. 
* Additional **miscellaneous equations** 

For more descriptions of GAMS components and syntax, see the `GAMS documentation <https://www.gams.com/latest/docs/>`_.

| **Objective equation**
| The objective equation of the model governs the central objective of the model. By default this is to minimise the total system cost, but it can be changed, as in the case of Modelling to Generate Alternatives (MGA).

The objective equation (``eq_obj``) and the total system cost is composed of generation, storage and transmission costs. Depending on the setup, start up costs (from UC) as well as penalty generation (value of lost load) may be included. Cost are divided into capital expenditure (Capex), fixed operation and maintenance costs (FOM) and variable operation and maintenance (VOM). There are no VOM costs included for transmission. 

.. math::
   :nowrap:

   \begin{gather*}
   \text{generation costs} = \sum_{g,z}(gen\_capex_{g} \times gen\_capacity_{g,z}) + \sum_{g,z,h}(gen\_VOM_{g,h} \times gen_{g,z,h}) + \\ \sum_{g,z}(gen\_FOM_{g} \times gen\_capacity_{g,z}) \\

   \text{storage costs} = \sum_{g,z}(store\_capex_{g} \times store\_capacity) + \sum_{g,z,h}(store\_gen_{g,z,h} \times store\_VOM_{g,h}) + \\ \sum_{g,z}(store\_FOM_{g} \times store\_capacity_{g,z}) \\

   \text{transmission costs} = \sum_{g,z}(trans\_capex_{g} \times trans\_cap_{g}) \\ + \sum_{g,z}(trans\_FOM_{g} \times trans\_cap_{g}) \\

   \text{penalty generation costs} = \sum_{z,h}(pgen\_cost \times pgen_{z,h}) \\
   \end{gather*}


The total system cost is then the sum of these different components, which, typically, are to be minimised. 

.. math::
   \min \text{total system cost} = \text{generation costs} + \text{storage costs} + \\ \text{transmission costs} + \text{penalty generation costs}

| **Demand balance equation**
| The demand balance equation (``eq_elc_balance(h,z)``) ensures that the demand is met in each of the zones (*z*) and for every hour (*h*) of the model. The demand can be met by in-region electricity generation, imported electricity from neighbouring regions through transmission infrastructure or discharging either of the storage technologies. At a high cost, the model can, if penalty generation is turned on, shed load. 

| **Transmission equations**
| The electricity transmission of highRES is represented using a computationally efficient linear transshipment formulation, where electricity flows similarly to fuel transport in pipelines. The benefit with a transshipment formulation compared to e.g. an *direct current optimal flow model* is that it is simpler `(Matar and Elshurafa, 2019) <https://doi.org/10.1016/j.egyr.2018.04.004>`_. 

The flow of electricity is constrained to not exceed the transmission capacity (``eq_trans_flow``) and bidirectionality is required (``eq_trans_bidirect``).  

| **Miscellaneous equations**
| One important miscellaneous equation is the CO₂ constraint (``eq_co2_budget``). It limits the total CO₂ emissions to be lower than a given value. The constraints scale with demand and as such indicate a maximum average emission intensity. By default, the intensity is 2gCO₂/kWh. 

Additionally, the model includes a set of submodules, containing various features. In general, these can be controlled by an IF statement. 

.. _data-input-label:

Module for data input 
~~~~~~~~~~~~~~~~~~~~~~

Whereas **highres.gms** contains the essential variables and equations, the data input submodule (``highres_data_input.gms``) contains the data input. This includes, among other things, the demand, the generation, the storage and the transmission data.

.. code-block:: gams

   $INCLUDE highres_data_input.gms

Within ``highres_data_input.gms`` numerous data files are loaded, such as for the defined spatial levels (regions and zones) as well as the temporal extent, technoeconomic generation and transmission data as well as the demand data. These are generated in the :ref:`workflow <workflow-label>`. 

The files are loaded through the following code:

::

       r regions /
       $BATINCLUDE %datafolderpath%/%vre_restrict%_regions.dd
       /

       z zones /
       $BATINCLUDE %datafolderpath%/zones.dd
       /

       $INCLUDE %datafolderpath%/%weather_yr%_temporal.dd

       $INCLUDE %datafolderpath%/%psys_scen%_gen.dd

       $INCLUDE %datafolderpath%/trans.dd

       $INCLUDE %datafolderpath%/%esys_scen%_demand_%dem_yr%.dd


Note that ``%datafolderpath%``, and other % enclosed variables are defined through Snakemake (see :ref:`workflow <workflow-label>` for further details). 

Before we go through the contents of those files, we need to introduce an important set, namely *lt*. 

.. code-block:: gams
   Sets

   lt / UP, LO, FX /

*lt* defines three types of limits that are loaded together with the technoeconomic input data. These are the upper limit (UP), the lower limit (LO) and the fixed limit (FX). These are used, for example in ``parameter gen_lim_pcap_z(z,g,lt);``. For example, in the line ``DK.HydroRoR.UP 0.009`` in ``gen.dd``, the upper limit for the generation capacity of run-off-river hydropower in Denmark is set to 0.009. This means that the model is allowed to build up to 0.009 GW of run-off-river hydropower in Denmark. If on the contrary, UP would be replaced by FX, the model would be forced to build exactly 0.009 GW of run-off-river hydropower in Denmark. 

Now, to the input data files.

.. code-block:: gams

   r regions /
   $BATINCLUDE %datafolderpath%/%vre_restrict%_regions.dd
   /

The regions.dd file contains the regions, which are the lower spatial level. 

.. code-block:: gams

   z zones /
   $BATINCLUDE %datafolderpath%/zones.dd
   /
   ;

The zones.dd file contains the zones, which are the higher spatial level.

.. code-block:: gams

   $INCLUDE %datafolderpath%/%weather_yr%_temporal.dd

The temporal.dd file contains the set h, for the temporal dimension in the model. Typically, this is a range between 0 and 8759, representing the hours of the year. 

.. code-block:: gams

   $INCLUDE %datafolderpath%/%psys_scen%_gen.dd

The gen.dd file contain information on generation technologies and their characteristics. It includes the ``set g``, with the different generation technologies, as well as subsets for, among other things, which technologies are variable (``set_vre(g)``) or not (``set_nonvre(g)``). Additionally, there are power capacity limits and existing infrastructure through the parameter ``gen_lim_pcap_z`` and ``gen_exist_pcap_z``, respectively. Similarly, there are energy capacity limits (storage) and existing infrastructure for reservoir hydro through the parameter ``gen_lim_ecap_z`` and ``gen_exist_ecap_z``, respectively. 

There are a few additional parameters, such as emission factors (``gen_emisfac``), cost parameters (``gen_capex``, ``gen_varom``, ``gen_fom``, ``gen_fuelcost``) and features related to unit commitment, if that is turned on. 

.. code-block:: gams

   $INCLUDE %datafolderpath%/trans.dd

The trans.dd file contains the ``set trans`` which includes the types of transmission technologies (typically HVAC400KV and HVDCSubsea) as well as the transmission links available to the model ``set trans_links`` and their associated distance ``parameter trans_links_dist`` and capacity limit ``parameter trans_links_cap``. 

.. code-block:: gams

      $INCLUDE %datafolderpath%/%esys_scen%_demand_%dem_yr%.dd

This file contains the demand, stored in the parameter ``demand(z,h)``. The demand is given in MWh for every hour and zone.

Module for storage
~~~~~~~~~~~~~~~~~~~~~~~~

The option of modelling storage in highRES is controlled in the $setglobal statement, whereas the IF statement loads the external storage submodule.

.. code-block:: gams

   $setglobal storage "ON"

   $IF "%storage%" == ON $INCLUDE highres_storage_setup.gms

By default, storage is turned on. 

A few important equations is the storage balance equation, the maximum storage level constraint and the storage end constraint.

The storage balance equation (``eq_store_balance(h,s_lim(z,s))``) models the storage level of each storage technology (*s*) for every hour (*h*) and zone (*z*). Essentially, the storage level (``var_store_level(h,z,s)``) is based on the electricity of the previous hour, with additionally stored electricity going into the storage level and electricity used for consumption subtracted from it. Additionally, there are efficiency losses and self-discharge. 

The storage level is constrained (``eq_store_level(s_lim(z,s),h)``) to always be lower or equal to the maximum storage capacity. Furthermore, the storage technologies are set to be cyclical (``eq_store_end_level``), meaning that they are not necessarily empty in the first hour of the model, but that they need to end at the same level as they started. 

**Extension for electric vehicle (EV) flexibility**

The extension for EV flexibility allows modelling EVs in the storage module by passing ``EV: "ON"`` in the config. EVs can be modelled with three distinct charging modes: immediate, flexible and bidirectional charging. The following switches in the config file adjust the EV charging behavior:

* EV (ON/OFF) = Whether to enable (ON) or disable (OFF) modelling of EV charging through the storage module. 

* EV_scenario = Specifies which EV scenario to consider. The scenario defines the number of cars and their average battery storage energy capacity for each modelled region.

* EV_flex (%) = The switch allows specifying the participation rate (as percentage) of EVs in flexible charging.

* V2G (ON/OFF) = Whether bidirectional charging (vehicle-to-grid (V2G)) is enabled (ON) or disabled (OFF).

* EV_soc_min = Minimum EV battery state-of-charge (SOC).

* EV_soc_max = Maximum EV battery state-of-charge (SOC).

The Snakemake rule 'build_ev_inputs' prepares input data for the EV modelling.

.. _reservoir-hydropower-label:

Module for reservoir hydropower
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Whereas run-off-river hydropower functions the same as other VREs, reservoir hydropower functions differently. Again, the $setglobal controls whether it is activated or not, and the IF statement loads the submodule (``highres_hydro.gms``).

.. code-block:: gams
    
   $setglobal hydrores "ON"

    $IF "%hydrores%" == ON $INCLUDE highres_hydro.gms

Reservoir hydropower functions similar to a storage technology, but with a natural inflow of energy (electricity) ``parameter hydro_inflow(h,z,hydro_res)``, as opposed to charging electricity from the grid. The storage level ``var_hydro_level`` at any given hour is the storage level in the previous hour, plus the inflow of water (in energy units), minus the electricity generated and water which is "spilled" if it is necessary to e.g. not overflow the reservoir. The inflow is loaded as an input, and generated in the :ref:`workflow <workflow-label>`. 

Additional equations ensure that the level of the reservoir does not exceed the maximum storage level ``eq_hydro_level(h,gen_lim(z,hydro_res))`` and not generate more electricity than the maximum power capacity ``eq_hydro_gen_max(h,gen_lim(z,hydro_res))``.

highRES does not include any cascading effects, meaning that the outflow of one reservoir is not the inflow of another. Rather, the model sees one large reservoir at the zonal or regional level, depending on the setup. However, the hydro power inflow is normalised, based on historical production data, to ensure that the total electricity available corresponds with reality. See the :ref:`workflow <workflow-label>` for more details. 


References
-------------

Matar, W., & Elshurafa, A. M. (2018). Electricity transmission formulations in multi-sector national planning models: An illustration using the KAPSARC energy model. Energy Reports, 4, 328–340. https://doi.org/10.1016/j.egyr.2018.04.004
