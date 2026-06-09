<?xml version='1.0' encoding='utf-8'?>
<TS version="2.1" language="zh-Hant">
    <context>
        <name>BoxPlot</name>
        <message>
            <source>Plots</source>
            <translation>Plots</translation>
        </message>
    </context>
    <context>
        <name>DBManagerPlugin</name>
        <message>
            <source>&lt;warning&gt; This user has no privileges!</source>
            <translation>&lt;warning&gt; This user has no privileges!</translation>
        </message>
    </context>
    <context>
        <name>QGISAlgorithm</name>
        <message>
            <source>This algorithm takes a points layer and generates a polygon layer containing the voronoi polygons corresponding to those input points.
</source>
            <extracomment>qgis:voronoipolygons</extracomment>
            <translation>This algorithm takes a points layer and generates a polygon layer containing the voronoi polygons corresponding to those input points.
</translation>
        </message>
        <message>
            <source>This algorithm performs a validity check on the geometries of a vector layer.
The geometries are classified in three groups (valid, invalid and error), and a vector layer is generated with the features in each of these categories.
By default the algorithm uses the strict OGC definition of polygon validity, where a polygon is marked as invalid if a self-intersecting ring causes an interior hole. If the "Ignore ring self intersections" option is checked, then this rule will be ignored and a more lenient validity check will be performed.
</source>
            <extracomment>qgis:checkvalidity</extracomment>
            <translation>This algorithm performs a validity check on the geometries of a vector layer.
The geometries are classified in three groups (valid, invalid and error), and a vector layer is generated with the features in each of these categories.
By default the algorithm uses the strict OGC definition of polygon validity, where a polygon is marked as invalid if a self-intersecting ring causes an interior hole. If the "Ignore ring self intersections" option is checked, then this rule will be ignored and a more lenient validity check will be performed.
</translation>
        </message>
    </context>
    <context>
        <name>QObject</name>
        <message numerus="yes">
            <location filename="../src/core/expression/qgsexpressionfunction.cpp" line="1724" />
            <source>Function `attribute` requires one or two parameters. %n given.</source>
            <translation>
                <numerusform>Function `attribute` requires one or two parameters. %n given.</numerusform>
                <numerusform>Function `attribute` requires one or two parameters. %n given.</numerusform>
            </translation>
        </message>
        <message>
            <location filename="../src/core/vector/qgsvectorlayerexporter.cpp" line="409" />
            <source>Import was canceled at %1 of %2</source>
            <translation>Import was canceled at %1 of %2</translation>
        </message>
        <message>
            <location filename="../src/core/providers/ogr/qgsogrproviderutils.cpp" line="446" />
            <source>UK. NTF2</source>
            <translation>UK. NTF2</translation>
        </message>
        <message>
            <location filename="../src/providers/postgres/qgspostgresdataitems.cpp" line="71" />
            <source>Unable to delete view %1: 
%2</source>
            <translation>Unable to delete view %1: 
%2</translation>
        </message>
        <message>
            <location filename="../src/gui/vector/qgsvectorlayerproperties.cpp" line="1250" />
            <source>Save style to DB (%1)</source>
            <translation>Save style to DB (%1)</translation>
        </message>
        <message numerus="yes">
            <location filename="../src/analysis/processing/qgsalgorithmangletonearest.cpp" line="269" />
            <source>Multiple matching features found at same distance from search feature, found %n feature(s)</source>
            <translation>
                <numerusform>Multiple matching features found at same distance from search feature, found %n feature(s)</numerusform>
                <numerusform>Multiple matching features found at same distance from search feature, found %n feature(s)</numerusform>
            </translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmreclassifybylayer.cpp" line="205" />
            <source>Invalid field specified for MIN_FIELD: %1</source>
            <translation>Invalid field specified for MIN_FIELD: %1</translation>
        </message>
        <message>
            <location filename="../src/core/qgsstatisticalsummary.cpp" line="337" />
            <source>Last</source>
            <translation>Last</translation>
        </message>
        <message>
            <location filename="../src/gui/auth/qgsauthguiutils.cpp" line="240" />
            <source>Cached authentication configurations for session cleared</source>
            <translation>Cached authentication configurations for session cleared</translation>
        </message>
        <message numerus="yes">
            <location filename="../src/gui/qgssqlcomposerdialog.cpp" line="448" />
            <source>%n argument(s)</source>
            <translation>
                <numerusform>%n argument(s)</numerusform>
                <numerusform>%n argument(s)</numerusform>
            </translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmjoinwithlines.cpp" line="91" />
            <source>This algorithm creates hub and spoke diagrams by connecting lines from points on the Spoke layer to matching points in the Hub layer.

Determination of which hub goes with each point is based on a match between the Hub ID field on the hub points and the Spoke ID field on the spoke points.

If input layers are not point layers, a point on the surface of the geometries will be taken as the connecting location.

Optionally, geodesic lines can be created, which represent the shortest path on the surface of an ellipsoid. When geodesic mode is used, it is possible to split the created lines at the antimeridian (±180 degrees longitude), which can improve rendering of the lines. Additionally, the distance between vertices can be specified. A smaller distance results in a denser, more accurate line.</source>
            <translation>This algorithm creates hub and spoke diagrams by connecting lines from points on the Spoke layer to matching points in the Hub layer.

Determination of which hub goes with each point is based on a match between the Hub ID field on the hub points and the Spoke ID field on the spoke points.

If input layers are not point layers, a point on the surface of the geometries will be taken as the connecting location.

Optionally, geodesic lines can be created, which represent the shortest path on the surface of an ellipsoid. When geodesic mode is used, it is possible to split the created lines at the antimeridian (±180 degrees longitude), which can improve rendering of the lines. Additionally, the distance between vertices can be specified. A smaller distance results in a denser, more accurate line.</translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmmergevector.cpp" line="62" />
            <source>This algorithm combines multiple vector layers of the same geometry type into a single one.

The attribute table of the resulting layer will contain the fields from all input layers. If fields with the same name but different types are found then the exported field will be automatically converted into a string type field. New fields storing the original layer name and source are also added.

If any input layers contain Z or M values, then the output layer will also contain these values. Similarly, if any of the input layers are multi-part, the output layer will also be a multi-part layer.

Optionally, the destination coordinate reference system (CRS) for the merged layer can be set. If it is not set, the CRS will be taken from the first input layer. All layers will all be reprojected to match this CRS.</source>
            <translation>This algorithm combines multiple vector layers of the same geometry type into a single one.

The attribute table of the resulting layer will contain the fields from all input layers. If fields with the same name but different types are found then the exported field will be automatically converted into a string type field. New fields storing the original layer name and source are also added.

If any input layers contain Z or M values, then the output layer will also contain these values. Similarly, if any of the input layers are multi-part, the output layer will also be a multi-part layer.

Optionally, the destination coordinate reference system (CRS) for the merged layer can be set. If it is not set, the CRS will be taken from the first input layer. All layers will all be reprojected to match this CRS.</translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmsaveselectedfeatures.cpp" line="57" />
            <source>This algorithm creates a new layer with all the selected features in a given vector layer.

If the selected layer has no selected features, the newly created layer will be empty.</source>
            <translation>This algorithm creates a new layer with all the selected features in a given vector layer.

If the selected layer has no selected features, the newly created layer will be empty.</translation>
        </message>
        <message>
            <location filename="../src/app/dwg/qgsdwgimporter.cpp" line="360" />
            <source>AutoCAD 2013/2014/2015/2016/2017</source>
            <translation>AutoCAD 2013/2014/2015/2016/2017</translation>
        </message>
        <message>
            <location filename="../src/core/qgsproperty.cpp" line="116" />
            <source>double coord [&lt;b&gt;X,Y&lt;/b&gt;]</source>
            <translation>double coord [&lt;b&gt;X,Y&lt;/b&gt;]</translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmrasterstatistics.cpp" line="115" />
            <source>&lt;p&gt;Mean value: %1&lt;/p&gt;
</source>
            <translation>&lt;p&gt;Mean value: %1&lt;/p&gt;
</translation>
        </message>
        <message numerus="yes">
            <location filename="../src/analysis/processing/qgsalgorithmdetectdatasetchanges.cpp" line="425" />
            <source>%n feature(s) added</source>
            <translation>
                <numerusform>%n feature(s) added</numerusform>
                <numerusform>%n feature(s) added</numerusform>
            </translation>
        </message>
        <message numerus="yes">
            <location filename="../src/analysis/processing/qgsalgorithmdetectdatasetchanges.cpp" line="426" />
            <source>%n feature(s) deleted</source>
            <translation>
                <numerusform>%n feature(s) deleted</numerusform>
                <numerusform>%n feature(s) deleted</numerusform>
            </translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmshpencodinginfo.cpp" line="50" />
            <source>This algorithm extracts the attribute encoding information embedded in a Shapefile.

Both the encoding specified by an optional .cpg file and any encoding details present in the .dbf LDID header block are considered.</source>
            <translation>This algorithm extracts the attribute encoding information embedded in a Shapefile.

Both the encoding specified by an optional .cpg file and any encoding details present in the .dbf LDID header block are considered.</translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmexporttopostgresql.cpp" line="156" />
            <location filename="../src/analysis/processing/qgsalgorithmexporttopostgresql.cpp" line="178" />
            <source>Error exporting to PostGIS
%1</source>
            <translation>Error exporting to PostGIS
%1</translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmsplitvectorlayer.cpp" line="144" />
            <source>Creating layer: %1</source>
            <translation>Creating layer: %1</translation>
        </message>
        <message>
            <location filename="../src/core/project/qgsprojectservervalidator.cpp" line="33" />
            <source>Layer short name is not valid. It must start with an unaccented alphabetical letter, followed by any alphanumeric letters, dot, dash or underscore</source>
            <translation>Layer short name is not valid. It must start with an unaccented alphabetical letter, followed by any alphanumeric letters, dot, dash or underscore</translation>
        </message>
        <message>
            <location filename="../src/gui/numericformats/qgsnumericformatwidget.cpp" line="147" />
            <source>-180 to +180°</source>
            <translation>-180 to +180°</translation>
        </message>
        <message>
            <location filename="../src/analysis/processing/qgsalgorithmshortestline.cpp" line="79" />
            <source>Maximum number of neighbors</source>
            <translation>Maximum number of neighbors</translation>
        </message>
        <message>
            <location filename="../src/core/processing/qgsprocessingcontext.cpp" line="87" />
            <source>Feature (%1) from “%2” has invalid geometry. Please fix the geometry or change the “Invalid features filtering” option for this input or globally in Processing settings.</source>
            <translation>Feature (%1) from “%2” has invalid geometry. Please fix the geometry or change the “Invalid features filtering” option for this input or globally in Processing settings.</translation>
        </message>
    </context>
    <context>
        <name>QgisApp</name>
        <message>
            <location filename="../src/app/qgisapp.cpp" line="8010" />
            <source>Successfully saved scratch layer to &lt;a href="%1"&gt;%2&lt;/a&gt;</source>
            <translation>Successfully saved scratch layer to &lt;a href="%1"&gt;%2&lt;/a&gt;</translation>
        </message>
        <message>
            <location filename="../src/app/qgisapp.cpp" line="16523" />
            <source>%1 features on layer %2 duplicated
%3</source>
            <translation>%1 features on layer %2 duplicated
%3</translation>
        </message>
    </context>
    <context>
        <name>QgsActionMenu</name>
        <message>
            <location filename="../src/gui/qgsactionmenu.cpp" line="44" />
            <source>&amp;Actions</source>
            <translation>&amp;Actions</translation>
        </message>
    </context>
    <context>
        <name>QgsAlignRasterLayerConfigDialog</name>
        <message>
            <location filename="../src/app/qgsalignrasterdialog.cpp" line="416" />
            <source>Browse…</source>
            <translation>Browse…</translation>
        </message>
    </context>
    <context>
        <name>QgsAppFileItemGuiProvider</name>
        <message numerus="yes">
            <location filename="../src/app/browser/qgsinbuiltdataitemproviders.cpp" line="769" />
            <source>Could not delete %n file(s)</source>
            <translation>
                <numerusform>Could not delete %n file(s)</numerusform>
                <numerusform>Could not delete %n file(s)</numerusform>
            </translation>
        </message>
    </context>
    <context>
        <name>QgsAppLayerTreeViewMenuProvider</name>
        <message>
            <location filename="../src/app/qgsapplayertreeviewmenuprovider.cpp" line="221" />
            <source>&amp;Stretch Using Current Extent</source>
            <translation>&amp;Stretch Using Current Extent</translation>
        </message>
        <message>
            <location filename="../src/app/qgsapplayertreeviewmenuprovider.cpp" line="407" />
            <source>Actions on Selection (%1)</source>
            <translation>Actions on Selection (%1)</translation>
        </message>
        <message>
            <location filename="../src/app/qgsapplayertreeviewmenuprovider.cpp" line="551" />
            <source>Save &amp;As…</source>
            <translation>Save &amp;As…</translation>
        </message>
    </context>
    <context>
        <name>QgsAttributeForm</name>
        <message>
            <location filename="../src/gui/qgsattributeform.cpp" line="1952" />
            <source>&amp;Flash Features</source>
            <translation>&amp;Flash Features</translation>
        </message>
    </context>
    <context>
        <name>QgsAuthCertInfo</name>
        <message>
            <location filename="../src/gui/auth/qgsauthcertificateinfo.cpp" line="693" />
            <source>OCSP locations</source>
            <translation>OCSP locations</translation>
        </message>
    </context>
    <context>
        <name>QgsAuthPkiPathsEdit</name>
        <message>
            <location filename="../src/auth/pkipaths/gui/qgsauthpkipathsedit.cpp" line="78" />
            <source>%1 thru %2</source>
            <translation>%1 thru %2</translation>
        </message>
    </context>
    <context>
        <name>QgsExpression</name>
        <message>
            <source>coalesce(NULL, 2)</source>
            <translation>coalesce(NULL, 2)</translation>
        </message>
        <message>
            <source>[ 4, 5 ]</source>
            <translation>[ 4, 5 ]</translation>
        </message>
        <message>
            <source>ln(2.7182818284590452354)</source>
            <translation>ln(2.7182818284590452354)</translation>
        </message>
        <message>
            <source>A variable width buffer starting with a diameter of 0.5 and ending with a diameter of 0.2 along the linestring geometry.</source>
            <translation>A variable width buffer starting with a diameter of 0.5 and ending with a diameter of 0.2 along the linestring geometry.</translation>
        </message>
        <message>
            <source>Geometry parts are specified as an array of geometry parts.</source>
            <translation>Geometry parts are specified as an array of geometry parts.</translation>
        </message>
        <message>
            <source>z_min( geom_from_wkt( 'POINT ( 0 0 1 )' ) )</source>
            <translation>z_min( geom_from_wkt( 'POINT ( 0 0 1 )' ) )</translation>
        </message>
        <message>
            <source>an integer corresponding to the lightening factor:&lt;ul&gt;&lt;li&gt;if the factor is greater than 100, this function returns a lighter color (e.g., setting factor to 150 returns a color that is 50% brighter);&lt;/li&gt;&lt;li&gt;if the factor is less than 100, the return color is darker, but using the darker() function for this purpose is recommended;&lt;/li&gt;&lt;li&gt;if the factor is 0 or negative, the return value is unspecified.&lt;/li&gt;&lt;/ul&gt;</source>
            <translation>an integer corresponding to the lightening factor:&lt;ul&gt;&lt;li&gt;if the factor is greater than 100, this function returns a lighter color (e.g., setting factor to 150 returns a color that is 50% brighter);&lt;/li&gt;&lt;li&gt;if the factor is less than 100, the return color is darker, but using the darker() function for this purpose is recommended;&lt;/li&gt;&lt;li&gt;if the factor is 0 or negative, the return value is unspecified.&lt;/li&gt;&lt;/ul&gt;</translation>
        </message>
        <message>
            <source>a string specifying the return values handling. Valid options are:&lt;br&gt;&lt;ul&gt;&lt;li&gt;all: Default, all most common values are returned in an array.&lt;/li&gt;&lt;li&gt;any: Returns one of the most common values.&lt;/li&gt;&lt;li&gt;median: Returns the median of the most common values. Non arithmetic values are ignored.&lt;/li&gt;&lt;li&gt;real_majority: Returns the value which occurs more than half the size of the array.&lt;/li&gt;&lt;/ul&gt;</source>
            <translation>a string specifying the return values handling. Valid options are:&lt;br&gt;&lt;ul&gt;&lt;li&gt;all: Default, all most common values are returned in an array.&lt;/li&gt;&lt;li&gt;any: Returns one of the most common values.&lt;/li&gt;&lt;li&gt;median: Returns the median of the most common values. Non arithmetic values are ignored.&lt;/li&gt;&lt;li&gt;real_majority: Returns the value which occurs more than half the size of the array.&lt;/li&gt;&lt;/ul&gt;</translation>
        </message>
        <message>
            <source>array_minority(array(0,1,42,42), 'all')</source>
            <translation>array_minority(array(0,1,42,42), 'all')</translation>
        </message>
        <message>
            <source>to_date('29 juin, 2019','d MMMM, yyyy','fr')</source>
            <translation>to_date('29 juin, 2019','d MMMM, yyyy','fr')</translation>
        </message>
        <message>
            <source>Converts a string to integer number. Nothing is returned if a value cannot be converted to integer (e.g '123asd' is invalid).</source>
            <translation>Converts a string to integer number. Nothing is returned if a value cannot be converted to integer (e.g '123asd' is invalid).</translation>
        </message>
    </context>
    <context>
        <name>QgsGeorefConfigDialog</name>
        <message>
            <location filename="../src/app/georeferencer/qgsgeorefconfigdialog.cpp" line="46" />
            <source>ANSI A (Letter; 8.5x11 inches)</source>
            <translation>ANSI A (Letter; 8.5x11 inches)</translation>
        </message>
    </context>
    <context>
        <name>QgsGeoreferencerMainWindow</name>
        <message>
            <location filename="../src/app/georeferencer/qgsgeorefmainwindow.cpp" line="2094" />
            <source>%1</source>
            <translation>%1</translation>
        </message>
        <message numerus="yes">
            <location filename="../src/app/georeferencer/qgsgeorefmainwindow.cpp" line="2243" />
            <source>%1 transformation requires at least %n GCPs. Please define more.</source>
            <translation>
                <numerusform>%1 transformation requires at least %n GCPs. Please define more.</numerusform>
                <numerusform>%1 transformation requires at least %n GCPs. Please define more.</numerusform>
            </translation>
        </message>
    </context>
    <context>
        <name>QgsGrass</name>
        <message>
            <location filename="../src/providers/grass/qgsgrass.cpp" line="366" />
            <source>GRASS was not found in '%1' (GISBASE), provider and plugin will not work.</source>
            <translation>GRASS was not found in '%1' (GISBASE), provider and plugin will not work.</translation>
        </message>
    </context>
    <context>
        <name>QgsHanaSourceSelect</name>
        <message>
            <location filename="../src/providers/hana/qgshanasourceselect.cpp" line="287" />
            <source>XML files (*.xml *XML)</source>
            <translation>XML files (*.xml *XML)</translation>
        </message>
    </context>
    <context>
        <name>QgsHeatmapRendererWidgetBase</name>
        <message>
            <location filename="../src/ui/symbollayer/qgsheatmaprendererwidgetbase.ui" />
            <source>&lt;html&gt;&lt;head/&gt;&lt;body&gt;&lt;p&gt;&lt;span style=" font-style:italic;"&gt;Fastest&lt;/span&gt;&lt;/p&gt;&lt;/body&gt;&lt;/html&gt;</source>
            <translation>&lt;html&gt;&lt;head/&gt;&lt;body&gt;&lt;p&gt;&lt;span style=" font-style:italic;"&gt;Fastest&lt;/span&gt;&lt;/p&gt;&lt;/body&gt;&lt;/html&gt;</translation>
        </message>
    </context>
    <context>
        <name>QgsHtmlDataItem</name>
        <message>
            <location filename="../src/app/qgsappbrowserproviders.cpp" line="1346" />
            <source>&amp;Open File…</source>
            <translation>&amp;Open File…</translation>
        </message>
    </context>
    <context>
        <name>QgsLayerMetadataLocatorFilter</name>
        <message>
            <location filename="../src/app/locator/qgslayermetadatalocatorfilter.h" line="33" />
            <source>Search Layer Metadata</source>
            <translation>Search Layer Metadata</translation>
        </message>
    </context>
    <context>
        <name>QgsLayerTreeViewDefaultActions</name>
        <message>
            <location filename="../src/gui/layertree/qgslayertreeviewdefaultactions.cpp" line="114" />
            <source>Zoom to &amp;Selection</source>
            <translation>Zoom to &amp;Selection</translation>
        </message>
        <message>
            <location filename="../src/gui/layertree/qgslayertreeviewdefaultactions.cpp" line="172" />
            <source>&amp;Mutually Exclusive Group</source>
            <translation>&amp;Mutually Exclusive Group</translation>
        </message>
    </context>
    <context>
        <name>QgsLayerTreeViewLowAccuracyIndicatorProvider</name>
        <message>
            <location filename="../src/app/qgslayertreeviewlowaccuracyindicator.cpp" line="72" />
            <source>Based on %1, which has a limited accuracy of &lt;b&gt;at best %2 meters&lt;/b&gt;.</source>
            <translation>Based on %1, which has a limited accuracy of &lt;b&gt;at best %2 meters&lt;/b&gt;.</translation>
        </message>
    </context>
    <context>
        <name>QgsLayoutDesignerBase</name>
        <message>
            <location filename="../src/ui/layout/qgslayoutdesignerbase.ui" />
            <source>S&amp;mart Guides</source>
            <translation>S&amp;mart Guides</translation>
        </message>
        <message>
            <location filename="../src/ui/layout/qgslayoutdesignerbase.ui" />
            <source>Resize to &amp;Shortest</source>
            <translation>Resize to &amp;Shortest</translation>
        </message>
    </context>
    <context>
        <name>QgsLayoutLegendNodeWidget</name>
        <message>
            <location filename="../src/gui/layout/qgslayoutlegendwidget.cpp" line="1768" />
            <source>Insert expression</source>
            <translation>Insert expression</translation>
        </message>
    </context>
    <context>
        <name>QgsLayoutMapGridWidget</name>
        <message>
            <location filename="../src/gui/layout/qgslayoutmapgridwidget.cpp" line="837" />
            <source>Change Frame Thickness</source>
            <translation>Change Frame Thickness</translation>
        </message>
    </context>
    <context>
        <name>QgsMapToolSelectUtils::QgsMapToolSelectMenuActions</name>
        <message>
            <location filename="../src/app/qgsmaptoolselectutils.cpp" line="692" />
            <source>Select All (%1)</source>
            <translation>Select All (%1)</translation>
        </message>
    </context>
    <context>
        <name>QgsMeshRendererVectorSettingsWidgetBase</name>
        <message>
            <location filename="../src/ui/mesh/qgsmeshrenderervectorsettingswidgetbase.ui" />
            <source>Head Options</source>
            <translation>Head Options</translation>
        </message>
    </context>
    <context>
        <name>QgsMssqlDataItemGuiProvider</name>
        <message>
            <location filename="../src/providers/mssql/qgsmssqldataitemguiprovider.cpp" line="232" />
            <source>Table truncated successfully.</source>
            <translation>Table truncated successfully.</translation>
        </message>
    </context>
    <context>
        <name>QgsOptions</name>
        <message>
            <location filename="../src/app/options/qgsoptions.cpp" line="588" />
            <source>Meters</source>
            <translation>Meters</translation>
        </message>
    </context>
    <context>
        <name>QgsOptionsBase</name>
        <message>
            <location filename="../src/ui/qgsoptionsbase.ui" />
            <source>Specifies the change in zoom level with each move of the mouse wheel.
The bigger the number, the faster zooming with the mouse wheel will be.</source>
            <translation>Specifies the change in zoom level with each move of the mouse wheel.
The bigger the number, the faster zooming with the mouse wheel will be.</translation>
        </message>
    </context>
    <context>
        <name>QgsOracleProvider</name>
        <message>
            <location filename="../src/providers/oracle/qgsoracleprovider.cpp" line="1785" />
            <source>Renaming column %1 to %2 failed</source>
            <translation>Renaming column %1 to %2 failed</translation>
        </message>
        <message>
            <location filename="../src/providers/oracle/qgsoracleprovider.h" line="340" />
            <source>Oracle error: %1
Error: %2</source>
            <translation>Oracle error: %1
Error: %2</translation>
        </message>
    </context>
    <context>
        <name>QgsPdalIndexingTask</name>
        <message>
            <location filename="../src/providers/pdal/qgspdalindexingtask.cpp" line="181" />
            <location filename="../src/providers/pdal/qgspdalindexingtask.cpp" line="216" />
            <source>File %1 is already indexed</source>
            <translation>File %1 is already indexed</translation>
        </message>
    </context>
    <context>
        <name>QgsPluginDependenciesDialog</name>
        <message>
            <source>Plugin dependencies for &lt;b&gt;%s&lt;/b&gt;</source>
            <translation>Plugin dependencies for &lt;b&gt;%s&lt;/b&gt;</translation>
        </message>
    </context>
    <context>
        <name>QgsPluginInstaller</name>
        <message>
            <source>QGIS has detected an obsolete plugin that masks its more recent version shipped with this copy of QGIS. This is likely due to files associated with a previous installation of QGIS. Do you want to remove the old plugin right now and unmask the more recent version?</source>
            <translation>QGIS has detected an obsolete plugin that masks its more recent version shipped with this copy of QGIS. This is likely due to files associated with a previous installation of QGIS. Do you want to remove the old plugin right now and unmask the more recent version?</translation>
        </message>
        <message>
            <source>Error installing plugin dependency &lt;b&gt;%s&lt;/b&gt;: %s</source>
            <translation>Error installing plugin dependency &lt;b&gt;%s&lt;/b&gt;: %s</translation>
        </message>
    </context>
    <context>
        <name>QgsPluginManagerBase</name>
        <message>
            <location filename="../src/ui/qgspluginmanagerbase.ui" />
            <source>Plugin Repositories</source>
            <translation>Plugin Repositories</translation>
        </message>
    </context>
    <context>
        <name>QgsPostgresProvider</name>
        <message>
            <location filename="../src/providers/postgres/qgspostgresprovider.cpp" line="1681" />
            <source>Unable to execute the query.
The error message from the database was:
%1.
SQL: %2</source>
            <translation>Unable to execute the query.
The error message from the database was:
%1.
SQL: %2</translation>
        </message>
    </context>
    <context>
        <name>QgsProcessingAggregateWidgetWrapper</name>
        <message>
            <location filename="../src/gui/processing/qgsprocessingaggregatewidgetwrapper.cpp" line="394" />
            <source>an array of map items, each containing a 'name', 'type', 'aggregate' and 'input' value (and optional 'length' and 'precision' values).</source>
            <translation>an array of map items, each containing a 'name', 'type', 'aggregate' and 'input' value (and optional 'length' and 'precision' values).</translation>
        </message>
    </context>
    <context>
        <name>QgsProjectPropertiesBase</name>
        <message>
            <location filename="../src/ui/qgsprojectpropertiesbase.ui" />
            <source>Or&amp;ganization</source>
            <translation>Or&amp;ganization</translation>
        </message>
    </context>
    <context>
        <name>QgsPropertyOverrideButton</name>
        <message>
            <location filename="../src/gui/qgspropertyoverridebutton.cpp" line="799" />
            <source>&lt;b&gt;&lt;u&gt;Data defined override&lt;/u&gt;&lt;/b&gt;&lt;br&gt;</source>
            <translation>&lt;b&gt;&lt;u&gt;Data defined override&lt;/u&gt;&lt;/b&gt;&lt;br&gt;</translation>
        </message>
    </context>
    <context>
        <name>QgsQueryBuilderBase</name>
        <message>
            <location filename="../src/ui/qgsquerybuilderbase.ui" />
            <source>&lt;html&gt;&lt;head&gt;&lt;meta name="qrichtext" content="1" /&gt;&lt;style type="text/css"&gt;
p, li { white-space: pre-wrap; }
&lt;/style&gt;&lt;/head&gt;&lt;body style=" font-family:'Sans Serif'; font-size:9pt; font-weight:400; font-style:normal;"&gt;
&lt;p style=" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;"&gt;Retrieve &lt;span style=" font-weight:600;"&gt;all&lt;/span&gt; the record in the vector file (&lt;span style=" font-style:italic;"&gt;if the table is big, the operation can consume some time&lt;/span&gt;)&lt;/p&gt;&lt;/body&gt;&lt;/html&gt;</source>
            <translation>&lt;html&gt;&lt;head&gt;&lt;meta name="qrichtext" content="1" /&gt;&lt;style type="text/css"&gt;
p, li { white-space: pre-wrap; }
&lt;/style&gt;&lt;/head&gt;&lt;body style=" font-family:'Sans Serif'; font-size:9pt; font-weight:400; font-style:normal;"&gt;
&lt;p style=" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;"&gt;Retrieve &lt;span style=" font-weight:600;"&gt;all&lt;/span&gt; the record in the vector file (&lt;span style=" font-style:italic;"&gt;if the table is big, the operation can consume some time&lt;/span&gt;)&lt;/p&gt;&lt;/body&gt;&lt;/html&gt;</translation>
        </message>
    </context>
    <context>
        <name>QgsQuickMapSettings</name>
        <message>
            <location filename="../src/quickgui/qgsquickmapsettings.cpp" line="256" />
            <location filename="../src/quickgui/qgsquickmapsettings.cpp" line="286" />
            <source>Map Canvas rotation is not supported. Resetting from %1 to 0.</source>
            <translation>Map Canvas rotation is not supported. Resetting from %1 to 0.</translation>
        </message>
    </context>
    <context>
        <name>QgsRasterLayerPropertiesBase</name>
        <message>
            <location filename="../src/ui/qgsrasterlayerpropertiesbase.ui" />
            <source>&lt;html&gt;&lt;head/&gt;&lt;body&gt;&lt;p&gt;&lt;span style=" font-weight:600;"&gt;Changing this option does not modify the original data source or perform any reprojection of the raster layer. Rather, it can be used to override the layer's CRS within this project if it could not be detected or has been incorrectly detected.&lt;/span&gt;&lt;/p&gt;&lt;p&gt;The Processing “&lt;span style=" font-style:italic;"&gt;Warp (reproject)&lt;/span&gt;” tool should be used to reproject a raster source and permanently change the data source's CRS.&lt;/p&gt;&lt;/body&gt;&lt;/html&gt;</source>
            <translation>&lt;html&gt;&lt;head/&gt;&lt;body&gt;&lt;p&gt;&lt;span style=" font-weight:600;"&gt;Changing this option does not modify the original data source or perform any reprojection of the raster layer. Rather, it can be used to override the layer's CRS within this project if it could not be detected or has been incorrectly detected.&lt;/span&gt;&lt;/p&gt;&lt;p&gt;The Processing “&lt;span style=" font-style:italic;"&gt;Warp (reproject)&lt;/span&gt;” tool should be used to reproject a raster source and permanently change the data source's CRS.&lt;/p&gt;&lt;/body&gt;&lt;/html&gt;</translation>
        </message>
    </context>
    <context>
        <name>QgsRendererRulePropsWidget</name>
        <message numerus="yes">
            <location filename="../src/gui/symbology/qgsrulebasedrendererwidget.cpp" line="885" />
            <source>Filter returned %n feature(s)</source>
            <comment>number of filtered features</comment>
            <translation>
                <numerusform>Filter returned %n feature(s)</numerusform>
                <numerusform>Filter returned %n feature(s)</numerusform>
            </translation>
        </message>
    </context>
    <context>
        <name>QgsSearchQueryBuilder</name>
        <message>
            <location filename="../src/gui/qgssearchquerybuilder.cpp" line="72" />
            <source>&amp;Clear</source>
            <translation>&amp;Clear</translation>
        </message>
    </context>
    <context>
        <name>QgsTableEditorWidget</name>
        <message numerus="yes">
            <location filename="../src/gui/tableeditor/qgstableeditorwidget.cpp" line="113" />
            <source>Insert %n Row(s) Below</source>
            <translation>
                <numerusform>Insert %n Row(s) Below</numerusform>
                <numerusform>Insert %n Row(s) Below</numerusform>
            </translation>
        </message>
    </context>
    <context>
        <name>QgsTextFormatWidget</name>
        <message>
            <location filename="../src/gui/qgstextformatwidget.cpp" line="841" />
            <source>Value &amp;lt; 0 represents a scale closer than 1:1, e.g. -10 = 10:1&lt;br&gt;Value of 0 disables the specific limit.</source>
            <translation>Value &amp;lt; 0 represents a scale closer than 1:1, e.g. -10 = 10:1&lt;br&gt;Value of 0 disables the specific limit.</translation>
        </message>
    </context>
    <context>
        <name>QgsVectorElevationPropertiesWidget</name>
        <message>
            <location filename="../src/app/vector/qgsvectorelevationpropertieswidget.cpp" line="55" />
            <source>Fill Below</source>
            <translation>Fill Below</translation>
        </message>
    </context>
    <context>
        <name>QgsWMSSourceSelectBase</name>
        <message>
            <location filename="../src/ui/qgswmssourceselectbase.ui" />
            <source>&amp;New</source>
            <translation>&amp;New</translation>
        </message>
    </context>
    <context>
        <name>QgsWmsDataItemGuiProvider</name>
        <message>
            <location filename="../src/providers/wms/qgswmsdataitemguiproviders.cpp" line="52" />
            <source>Edit Connection…</source>
            <translation>Edit Connection…</translation>
        </message>
    </context>
    <context>
        <name>QgsWmsProvider</name>
        <message numerus="yes">
            <location filename="../src/providers/wms/qgswmsprovider.cpp" line="3731" />
            <source>Result parsing failed. %n feature type(s) were guessed from gml (%2) but no features were parsed.</source>
            <translation>
                <numerusform>Result parsing failed. %n feature type(s) were guessed from gml (%2) but no features were parsed.</numerusform>
                <numerusform>Result parsing failed. %n feature type(s) were guessed from gml (%2) but no features were parsed.</numerusform>
            </translation>
        </message>
        <message>
            <location filename="../src/providers/wms/qgswmsprovider.cpp" line="3120" />
            <source>Misses</source>
            <translation>Misses</translation>
        </message>
    </context>
    <context>
        <name>QgsWmsTiledImageDownloadHandler</name>
        <message numerus="yes">
            <location filename="../src/providers/wms/qgswmsprovider.cpp" line="4869" />
            <source>, %n cache hit(s)</source>
            <comment>tile cache hits</comment>
            <translation>
                <numerusform>, %n cache hit(s)</numerusform>
                <numerusform>, %n cache hit(s)</numerusform>
            </translation>
        </message>
    </context>
    <context>
        <name>Setting</name>
        <message>
            <source>Specified path does not exist:
{0}</source>
            <translation>Specified path does not exist:
{0}</translation>
        </message>
    </context>
    <context>
        <name>SettingsDialogPythonConsole</name>
        <message>
            <location filename="../python/console/console_settings.ui" />
            <source>&lt;PERSONAL_ACCESS_TOKEN&gt;</source>
            <translation>&lt;PERSONAL_ACCESS_TOKEN&gt;</translation>
        </message>
    </context>
    <context>
        <name>TilesXYZAlgorithmBase</name>
        <message>
            <source>Maximum zoom</source>
            <translation>Maximum zoom</translation>
        </message>
    </context>
    <context>
        <name>WidgetVectorFieldBase</name>
        <message>
            <location filename="../src/ui/symbollayer/widget_vectorfield.ui" />
            <source>Angle Orientation</source>
            <translation>Angle Orientation</translation>
        </message>
    </context>
    <context>
        <name>grasslabels</name>
        <message>
            <source>Calculates univariate statistics of attributes for each registered vector map of a space time vector dataset</source>
            <translation>Calculates univariate statistics of attributes for each registered vector map of a space time vector dataset</translation>
        </message>
    </context>
</TS>