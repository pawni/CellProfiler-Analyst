'''
Dependencies:
Enthought Tool Suite (for Mayavi2): http://www.lfd.uci.edu/~gohlke/pythonlibs/#ets
VTK (5.10+): http://www.lfd.uci.edu/~gohlke/pythonlibs/#vtk
NetworkX (1.7+): http://www.lfd.uci.edu/~gohlke/pythonlibs/#networkx
NumPy-MKL (1.71+): http://www.lfd.uci.edu/~gohlke/pythonlibs/#numpy
configobj (required by Enthought): https://pypi.python.org/pypi/configobj
'''
import wx
from wx.combo import OwnerDrawnComboBox as ComboBox
from wx.lib.scrolledpanel import ScrolledPanel
import networkx as nx
import numpy as np
from operator import itemgetter
import glayout

import logging
import time
import sortbin
import imagetools
from guiutils import get_main_frame_or_none
from dbconnect import DBConnect, image_key_columns, object_key_columns
from properties import Properties
from cpatool import CPATool
import tableviewer

import matplotlib
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg

# traits imports
from traits.api import HasTraits, Instance, on_trait_change
from traitsui.api import View, Item, HSplit, Group

# mayavi imports
from mayavi import mlab
from mayavi.core.ui.api import MlabSceneModel, SceneEditor
from mayavi.core.ui.mayavi_scene import MayaviScene
from tvtk.pyface.scene import Scene
from tvtk.api import tvtk

# Colormap names from an error msg (http://www.mail-archive.com/mayavi-users@lists.sourceforge.net/msg00615.html)
# TODO(?): Find a better way to captures these names
all_colormaps = ['Accent', 'Blues', 'BrBG', 'BuGn', 'BuPu', 'Dark2', 
                 'GnBu', 'Greens', 'Greys', 'OrRd', 'Oranges', 'PRGn', 
                 'Paired', 'Pastel1', 'Pastel2', 'PiYG', 'PuBu', 
                 'PuBuGn', 'PuOr', 'PuRd', 'Purples', 'RdBu', 'RdGy', 
                 'RdPu', 'RdYlBu', 'RdYlGn', 'Reds', 'Set1', 'Set2', 
                 'Set3', 'Spectral', 'YlGn', 'YlGnBu', 'YlOrBr', 
                 'YlOrRd', 'autumn', 'binary', 'black-white', 'blue-red', 
                 'bone', 'cool', 'copper', 'file', 'flag', 'gist_earth', 
                 'gist_gray', 'gist_heat', 'gist_ncar', 'gist_rainbow', 
                 'gist_stern', 'gist_yarg', 'gray', 'hot', 'hsv', 'jet', 
                 'pink', 'prism', 'spectral', 'spring', 'summer','winter']
all_colormaps.sort()

required_fields = ['series_id', 'group_id', 'timepoint_id','object_tracking_label']

track_attributes = ["label","x","y","t","s","f"]

db = DBConnect.getInstance()
props = Properties.getInstance()

TRACKING_MODULE_NAME = "TrackObjects"

def add_props_field(props):
    # Temp declarations; these will be retrieved from the properties file directly, eventually
    props.series_id = ["Image_Group_Number"]
    #props.series_id = ["Image_Metadata_Plate"]
    props.group_id = "Image_Group_Number"
    props.timepoint_id = "Image_Group_Index"
    obj = props.cell_x_loc.split('_Location_Center')[0]
    # TODO: Allow for selection of tracking labels, since there may be multiple objects tracked in different ways. Right now, just pick the first one.
    # TODO: Allow for selection of parent image/object fields, since there may be multiple tracked objects. Right now, just pick the first one.
    if props.db_type == 'sqlite':
        query = "PRAGMA table_info(%s)"%(props.object_table)
        all_fields = [item[1] for item in db.execute(query)]
    else:
        query = "SELECT column_name FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = '%s'"%(props.db_name, props.object_table)
        all_fields = [item[0] for item in db.execute(query)]
    props.object_tracking_label = [item for item in all_fields if item.find("_".join((obj,TRACKING_MODULE_NAME,'Label'))) != -1][0]
    props.parent_fields = [ [item for item in all_fields if item.find("_".join((obj,TRACKING_MODULE_NAME,'ParentImageNumber'))) != -1][0],
                            [item for item in all_fields if item.find("_".join((obj,TRACKING_MODULE_NAME,'ParentObjectNumber'))) != -1][0] ] 
    table_prefix = props.image_table.split("Per_Image")[0]
    props.relationship_table = table_prefix + "Per_Relationships"
    props.relationshiptypes_table = table_prefix + "Per_RelationshipTypes"
    props.relationships_view = table_prefix + "Per_RelationshipsView"
    return props

def retrieve_datasets():
    series_list = ",".join(props.series_id)
    query = "SELECT %s FROM %s GROUP BY %s"%(series_list,props.image_table,series_list)
    all_datasets = [x[0] for x in db.execute(query)]
    return all_datasets

def is_LAP_tracking_data():
    # If the data is LAP-based, then additional button(s) show up
    LAP_field = "Kalman"
    if props.db_type == 'sqlite':
        query = "PRAGMA table_info(%s)"%(props.object_table)
        return(len([item[1] for item in db.execute(query) if item[1].find(LAP_field) != -1]) > 0)
    else:
        query = "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = '%s' AND COLUMN_NAME REGEXP '_Kalman_'"%(props.db_name, props.object_table)
        return(len(db.execute(query)) > 0)
    
def obtain_tracking_data(selected_dataset, selected_measurement, selected_filter):
    def parse_dataset_selection(s):
        return [x.strip() for x in s.split(',') if x.strip() is not '']
    
    selection_list = parse_dataset_selection(selected_dataset)
    dataset_clause = " AND ".join(["%s = '%s'"%(x[0], x[1]) for x in zip([props.image_table+"."+item for item in props.series_id], selection_list)])
    
    columns_to_retrieve = list(object_key_columns(props.object_table))    # Node IDs
    columns_to_retrieve += [props.object_table+"."+item for item in props.parent_fields]    # Parent node IDs
    columns_to_retrieve += [props.object_table+"."+props.object_tracking_label] # Label assigned by TrackObjects
    columns_to_retrieve += [props.object_table+"."+props.cell_x_loc, props.object_table+"."+props.cell_y_loc] # x,y coordinates
    columns_to_retrieve += [props.image_table+"."+props.timepoint_id] # Timepoint/frame
    columns_to_retrieve += [props.object_table+"."+selected_measurement if selected_measurement is not None else 'NULL'] # Measured feature, insert NULL as placeholder if derived
    columns_to_retrieve += [" AND ".join(selected_filter)] if selected_filter is not None else ['1'] # Filter
    query = ["SELECT %s"%(",".join(columns_to_retrieve))]
    query.append("FROM %s, %s"%(props.image_table, props.object_table))
    query.append("WHERE %s = %s AND %s"%(props.image_table+"."+props.image_id, props.object_table+"."+props.image_id, dataset_clause))
    query.append("ORDER BY %s, %s"%(props.object_tracking_label, props.timepoint_id))
    data = db.execute(" ".join(query))
    columns = [props.object_tracking_label, props.image_id, props.object_id, props.cell_x_loc, props.cell_y_loc, props.timepoint_id, "Filter", props.parent_fields]
    
    return columns,data

################################################################################
class MeasurementFilter(wx.Panel):
    '''
    Widget for creating lists of filters
    '''    
    def __init__(self, parent, allow_delete=True, **kwargs):
        wx.Panel.__init__(self, parent, **kwargs)        
        
        self.measurement_choices = db.GetColumnNames(props.object_table)
        self.colChoice = ComboBox(self, choices=self.measurement_choices, size=(-1,-1), style=wx.CB_READONLY)
        self.colChoice.Select(0)
        self.colChoice.Bind(wx.EVT_COMBOBOX, self.on_select_column)
        
        self.comparatorChoice = ComboBox(self, size=(-1,-1))
        self.update_comparator_choice()
        
        self.valueField = wx.ComboBox(self, -1, value='')
        
        if allow_delete:
            self.minus_button = wx.Button(self, -1, label='-', size=(30,-1))
            self.minus_button.Bind(wx.EVT_BUTTON, lambda event: self.Parent.on_remove_filter(event,self))              
        self.plus_button = wx.Button(self, -1, label='+', size=(30,-1))   
        self.plus_button.Bind(wx.EVT_BUTTON, lambda event: self.Parent.on_add_filter(event,self))     
        
        colSizer = wx.BoxSizer(wx.HORIZONTAL)
        colSizer.Add(self.colChoice, 1, wx.EXPAND)
        colSizer.AddSpacer((5,-1))
        colSizer.Add(self.comparatorChoice, 1, wx.EXPAND)
        colSizer.AddSpacer((5,-1))
        colSizer.Add(self.valueField, 1, wx.EXPAND)
        colSizer.AddSpacer((5,-1))
        colSizer.Add(self.plus_button, 0, wx.EXPAND) 
        colSizer.AddSpacer((5,-1))        
        colSizer.Add(self.minus_button if allow_delete else wx.StaticText(self,-1,size=(30,-1)), 0, wx.EXPAND)
        self.SetSizerAndFit(colSizer)

    def on_select_column(self, evt):
        self.update_comparator_choice()
        self.update_value_choice()

    def _get_column_type(self):
        return db.GetColumnTypes(props.object_table)[self.colChoice.GetSelection()]

    def update_comparator_choice(self):
        coltype = self._get_column_type()
        comparators = []
        if coltype in [str, unicode]:
            comparators = ['=', '!=', 'REGEXP', 'IS', 'IS NOT', 'IS NULL']
        if coltype in [int, float, long]:
            comparators = ['=', '!=', '<', '>', '<=', '>=', 'IS', 'IS NOT', 'IS NULL']
        self.comparatorChoice.SetItems(comparators)
        self.comparatorChoice.Select(0)
        
    def update_value_choice(self):
        column = self.colChoice.Value
        column_type = db.GetColumnTypes(props.object_table)[self.colChoice.GetSelection()]
        vals = []
        if column_type == str:# or coltype == int or coltype == long:
            res = db.execute('SELECT DISTINCT %s FROM %s ORDER BY %s'%(column, table, column))
            vals = [str(row[0]) for row in res]
        self.valueField.SetItems(vals)         

################################################################################
class FilterPanel(ScrolledPanel):
    '''
    Panel for measurement filtering.
    '''
    def __init__(self, parent, **kwargs):
        ScrolledPanel.__init__(self, parent, **kwargs)
        
        self.panel_sizer = wx.BoxSizer( wx.VERTICAL )
        self.filters = []
        filt = MeasurementFilter(self, False)
        self.panel_sizer.Add(filt, 0, wx.EXPAND)
        self.filters.append(filt)

        self.SetSizer(self.panel_sizer)
        self.SetAutoLayout(1)
        self.SetupScrolling(False,True)
        self.Disable()
        
    def on_add_filter(self,event,selected_filter):
        self.filters.append(MeasurementFilter(self, True))
        self.panel_sizer.Add(self.filters[-1], 0, wx.EXPAND|wx.BOTTOM|wx.LEFT|wx.RIGHT, 5)
        self.SetupScrolling(False,True)
        self.panel_sizer.SetMinSize(self.panel_sizer.GetMinSize())
        self.SetSizerAndFit(self.panel_sizer)        
        self.SetAutoLayout(1)
        self.Refresh()
        self.Layout() 
        
    def on_remove_filter(self,event,selected_filter):
        i = self.filters.index(selected_filter)
        self.filters.remove(selected_filter)
        self.panel_sizer.Remove(selected_filter)
        selected_filter.Destroy()
        self.SetupScrolling(False,len(self.filters) < 3 )  
        self.Refresh()
        self.Layout()          

################################################################################
class TimeLapseControlPanel(wx.Panel):
    '''
    A panel with controls for selecting the data for a visual
    Some helpful tips on using sizers for layout: http://zetcode.com/wxpython/layout/
    '''

    def __init__(self, parent, **kwargs):
        wx.Panel.__init__(self, parent, **kwargs)

        # Get names of data sets
        all_datasets = retrieve_datasets()

        # Capture if LAP data is being used
        self.isLAP = is_LAP_tracking_data()
        
        # Get names of fields
        measurements = db.GetColumnNames(props.object_table)
        coltypes = db.GetColumnTypes(props.object_table)
        fields = [m for m,t in zip(measurements, coltypes) if t in [float, int, long]]
        self.dataset_measurement_choices = fields
        
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Define widgets
        self.dataset_choice = ComboBox(self, -1, choices=[str(item) for item in all_datasets], size=(200,-1), style=wx.CB_READONLY)
        self.dataset_choice.Select(0)
        self.dataset_choice.SetHelpText("Select the time-lapse data set to visualize.")
        self.measurement_choice = ComboBox(self, -1, choices=self.dataset_measurement_choices, style=wx.CB_READONLY)
        self.measurement_choice.Select(0)
        self.measurement_choice.SetHelpText("Select the per-%s measurement to visualize the data with. The lineages and (xyt) trajectories will be color-coded by this measurement."%props.object_name[0])
        self.colormap_choice = ComboBox(self, -1, choices=all_colormaps, style=wx.CB_READONLY)
        self.colormap_choice.SetStringSelection("jet") 
        self.colormap_choice.SetHelpText("Select the colormap to use for color-coding the data.")
        self.trajectory_selection_button = wx.Button(self, -1, "Select Tracks to Visualize...")
        if self.isLAP:
            self.trajectory_diagnosis_toggle = wx.ToggleButton(self, -1, "Show LAP Diagnostic Graphs")
            self.trajectory_diagnosis_toggle.SetHelpText("If you have tracking data generated by the LAP method, a new box will open with diagnostic graphs indicating goodness of your settings.")
        self.trajectory_selection_button.SetHelpText("Select the trajectories to show or hide in both panels.")
        self.update_plot_color_button = wx.Button(self, -1, "Update Color")
        self.update_plot_color_button.SetHelpText("Press this button after making selections to update the panels.")
        self.help_button = wx.ContextHelpButton(self)

        # Arrange widgets
        # Row #1: Dataset drop-down + track selection button
        sz = wx.BoxSizer(wx.HORIZONTAL)
        sz.Add(wx.StaticText(self, -1, "Data Source:"), 0, wx.TOP, 4)
        sz.AddSpacer((4,-1))
        sz.Add(self.dataset_choice, 1, wx.EXPAND)
        sz.AddSpacer((4,-1))
        sz.Add(self.trajectory_selection_button)
        if self.isLAP:
            sz.AddSpacer((4,-1))
            sz.Add(self.trajectory_diagnosis_toggle)
        sizer.Add(sz, 1, wx.EXPAND)
        sizer.AddSpacer((-1,2))

        # Row #2: Measurement color selection, colormap, update button
        sz = wx.BoxSizer(wx.HORIZONTAL)
        sz.Add(wx.StaticText(self, -1, "Color by Measurement:"), 0, wx.TOP, 4)
        sz.AddSpacer((4,-1))
        sz.Add(self.measurement_choice, 1, wx.EXPAND)
        sz.AddSpacer((4,-1))
        sz.Add(wx.StaticText(self, -1, "Colormap:"), 0, wx.TOP, 4)
        sz.AddSpacer((4,-1))
        sz.Add(self.colormap_choice, 1, wx.EXPAND)
        sz.AddSpacer((4,-1))
        sz.Add(self.update_plot_color_button)
        sz.AddSpacer((4,-1))
        sz.Add(self.help_button)
        sizer.Add(sz, 1, wx.EXPAND)
        sizer.AddSpacer((-1,2))

        # Row #3: Measurement filter selection
        sz = wx.BoxSizer(wx.HORIZONTAL)
        self.enable_filtering_checkbox = wx.CheckBox(self, -1, label="Enable filtering")
        self.enable_filtering_checkbox.SetValue(0)
        sz.Add(self.enable_filtering_checkbox, 0, wx.TOP, 4)
        sz.AddSpacer((4,-1))
        self.filter_panel = FilterPanel(self)
        sz.Add(self.filter_panel,1, wx.TOP, 4)
        sz.Layout()
        sizer.Add(sz, 1, wx.EXPAND) 
        sizer.AddSpacer((-1,2))
        sizer.Layout()
        self.SetSizer(sizer)
        self.Layout()
        self.Show(True)
        
################################################################################
class MayaviView(HasTraits):
    """ Create a mayavi scene"""
    lineage_scene = Instance(MlabSceneModel, ())
    trajectory_scene = Instance(MlabSceneModel, ())
    
    # The layout of the dialog created
    view = View(HSplit(Group(Item('trajectory_scene',
                                  #editor = SceneEditor(scene_class = Scene),
                                  editor = SceneEditor(scene_class=MayaviScene),
                                  resizable=True, show_label=False)),
                       Group(Item('lineage_scene',
                                  editor = SceneEditor(scene_class = Scene),
                                  #editor = SceneEditor(scene_class=MayaviScene),
                                  resizable=True, show_label=False))),
                resizable=True)
    
    def __init__(self):
        HasTraits.__init__(self)
        #self.figure = self.lineage_scene.mlab.gcf()
        
    @on_trait_change('scene.activated')
    def update_plot(self):
        picker = self.self.lineage_scene.mlab.gcf().on_mouse_pick(self.on_pick_one_timepoint,
                                                                    type='point',
                                                                                  button='Left',
                                                                                  remove=False)
        picker.tolerance = 0.01        

################################################################################
class FigureFrame(wx.Frame, CPATool):
    """A wx.Frame with a figure inside"""
    def __init__(self, parent=None, id=-1, title="", 
                     pos=wx.DefaultPosition, size=wx.DefaultSize,
                     style=wx.DEFAULT_FRAME_STYLE, name=wx.FrameNameStr, 
                     subplots=None, on_close = None):
        """Initialize the frame:
            
            parent   - parent window to this one
            id       - window ID
            title    - title in title bar
            pos      - 2-tuple position on screen in pixels
            size     - 2-tuple size of frame in pixels
            style    - window style
            name     - searchable window name
            subplots - 2-tuple indicating the layout of subplots inside the window
            on_close - a function to run when the window closes
            """       
        super(FigureFrame,self).__init__(parent, id, title, pos, size, style, name)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(sizer) 
        matplotlib.rcdefaults()
        self.figure = figure = matplotlib.figure.Figure()
        figure.set_facecolor((1,1,1))
        figure.set_edgecolor((1,1,1))        
        self.panel = FigureCanvasWxAgg(self, -1, self.figure)
        sizer.Add(self.panel, 1, wx.EXPAND)
        #wx.EVT_CLOSE(self, self.on_close)
        if subplots:
            self.subplots = np.zeros(subplots,dtype=object)  
        self.Fit()
        self.Show()
        
    def on_close(self, event):
        if self.close_fn is not None:
            self.close_fn(event)
        self.clf() # Free memory allocated by imshow
        self.Destroy()        
        
################################################################################
class TimeLapseTool(wx.Frame, CPATool):
    '''
    A time-lapse visual plot with its controls.
    '''
    def __init__(self, parent, size=(1000,600), **kwargs):
        wx.Frame.__init__(self, parent, -1, size=size, title='Time-Lapse Tool', **kwargs)
        CPATool.__init__(self)
        wx.HelpProvider_Set(wx.SimpleHelpProvider())
        self.SetName(self.tool_name)
        
        # Check for required properties fields.
        #fail = False
        #missing_fields = [field for field in required_fields if not props.field_defined(field)]
        #if missing_fields:
            #fail = True
            #message = 'The following missing fields are required for LineageTool: %s.'%(",".join(missing_fields))
            #wx.MessageBox(message,'Required field(s) missing')
            #logging.error(message)
        #if fail:    
            #self.Destroy()
            #return   
        props = Properties.getInstance()
        props = add_props_field(props)

        self.control_panel = TimeLapseControlPanel(self)
        self.selected_dataset = self.control_panel.dataset_choice.GetStringSelection()
        self.dataset_measurement_choices = self.control_panel.measurement_choice.GetItems()
        self.selected_measurement = self.control_panel.measurement_choice.GetStringSelection()
        self.selected_colormap  = self.control_panel.colormap_choice.GetStringSelection()
        self.selected_filter = None
        self.plot_updated = False
        self.trajectory_selected = False
        self.selected_node = None
        self.selected_endpoints = [None,None]
        self.axes_opacity = 0.25
        self.do_plots_need_updating = {"dataset":True,
                                       "colormap":True,
                                       "measurement":True, 
                                       "trajectories":True,
                                       "filter":None}
        
        self.mayavi_view = MayaviView()
        self.figure_panel = self.mayavi_view.edit_traits(
                                            parent=self,
                                            kind='subpanel').control
        navigation_help_text = ("Tips on navigating the plots:\n"
                                "Rotating the 3-D visualization: Place the mouse pointer over the visualization"
                                "window. Then left-click and drag the mouse pointer in the direction you want to rotate"
                                "the scene, much like rotating an actual object.\n\n"
                                "Zooming in and out: Place the mouse pointer over the visualization"
                                "window. To zoom into the scene, keep the right mouse button pressed and"
                                "drags the mouse upwards. To zoom out of the scene,  keep the right mouse button pressed"
                                "and drags the mouse downwards.\n\n"
                                "Panning: This can be done in one in two ways:\n"
                                "1. Keep the left mouse button pressed and simultaneously holding down the Shift key"
                                "and dragging the mouse in the appropriate direction.\n"
                                "2. Keep the middle mouse button pressed and dragging the mouse in the appropriate"
                                "direction\n\n"
                                "Please note that while the lineage panel can be rotated, zoomed and panned, it is a 2-D"
                                "plot so the top-down view is fixed.")
        self.figure_panel.SetHelpText(navigation_help_text)
        
        self.update_plot()
            
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.figure_panel, 1, wx.EXPAND)
        sizer.Add(self.control_panel, 0, wx.EXPAND|wx.ALL, 5)
        self.SetSizer(sizer)
        
        self.figure_panel.Bind(wx.EVT_CONTEXT_MENU, self.on_show_popup_menu)
        
        # Define events
        wx.EVT_COMBOBOX(self.control_panel.dataset_choice, -1, self.on_dataset_selected)
        wx.EVT_COMBOBOX(self.control_panel.measurement_choice, -1, self.on_measurement_selected)
        wx.EVT_BUTTON(self.control_panel.trajectory_selection_button, -1, self.update_trajectory_selection)
        wx.EVT_TOGGLEBUTTON(self.control_panel.trajectory_diagnosis_toggle, -1, self.calculate_and_display_lap_stats)
        wx.EVT_COMBOBOX(self.control_panel.colormap_choice, -1, self.on_colormap_selected)
        wx.EVT_BUTTON(self.control_panel.update_plot_color_button, -1, self.update_plot)
        wx.EVT_CHECKBOX(self.control_panel.enable_filtering_checkbox, -1, self.enable_filtering)
        
    def on_show_all_trajectories(self, event = None):
        self.trajectory_selection = dict.fromkeys(self.connected_nodes.keys(),1)
        self.do_plots_need_updating["trajectories"] = True
        self.update_plot()    

    def on_show_popup_menu(self, event = None):   
        '''
        Event handler: show the viewer context menu.  
        
        @param event: the event binder
        @type event: wx event
        '''
        class TrajectoryPopupMenu(wx.Menu):
            '''
            Build the context menu that appears when you right-click on a trajectory
            '''
            def __init__(self, parent):
                super(TrajectoryPopupMenu, self).__init__()
                
                self.parent = parent
            
                # The 'Show data in table' item and its associated binding
                if self.parent.selected_node is not None:
                    item = wx.MenuItem(self, wx.NewId(), "Show data containing %s %s in table"%(props.object_name[0],str(self.parent.selected_node)))
                    self.AppendItem(item)
                    self.Bind(wx.EVT_MENU, self.parent.show_selection_in_table, item)
                    
                    item = wx.MenuItem(self, wx.NewId(), "Use %s %s as endpoint #1 for a montage"%(props.object_name[0],str(self.parent.selected_node)))
                    self.AppendItem(item)
                    self.Bind(wx.EVT_MENU, self.parent.select_point1_for_montage, item)
                    
                    item = wx.MenuItem(self, wx.NewId(), "Use %s %s as endpoint #2 for a montage"%(props.object_name[0],str(self.parent.selected_node)))
                    self.AppendItem(item)
                    self.Bind(wx.EVT_MENU, self.parent.select_point2_for_montage, item)
                    
                    
                    item = wx.MenuItem(self, wx.NewId(), "Show image montage containing selected endpoints")
                    item.Enable(all([_ != None for _ in self.parent.selected_endpoints]))
                    self.AppendItem(item)
                    self.Bind(wx.EVT_MENU, self.parent.show_cell_montage, item)  
                    
                    item = wx.MenuItem(self, wx.NewId(), "Show image tile of %s %s"%(props.object_name[0],str(self.parent.selected_node)))
                    self.AppendItem(item)
                    self.Bind(wx.EVT_MENU, self.parent.show_cell_tile, item)    
                    
                    item = wx.MenuItem(self, wx.NewId(), "Show full image containing %s %s"%(props.object_name[0],str(self.parent.selected_node)))
                    self.AppendItem(item)
                    self.Bind(wx.EVT_MENU, self.parent.show_full_image, item)                        
                # The 'Show all trajectories' item and its associated binding
                item = wx.MenuItem(self, wx.NewId(), "Show all trajectories")
                self.AppendItem(item)
                self.Bind(wx.EVT_MENU, self.parent.on_show_all_trajectories, item)

        # The event (mouse right-click) position.
        pos = event.GetPosition()
        # Converts the position to mayavi internal coordinates.
        pos = self.figure_panel.ScreenToClient(pos)                                                        
        # Show the context menu.      
        self.PopupMenu(TrajectoryPopupMenu(self), pos)    

    def show_selection_in_table(self, event = None):
        '''Callback for "Show selection in a table" popup item.'''
        keys = [self.connected_nodes[item].nodes() for item in self.selected_trajectory]
        keys = [item for sublist in keys for item in sublist]
        tracking_label,timepoint,data = zip(*np.array([(self.directed_graph.node[node]["label"],self.directed_graph.node[node]["t"],self.directed_graph.node[node]["s"]) for node in keys]))
        table_data = np.hstack((np.array(keys), np.array((tracking_label,timepoint,data)).T))
        column_labels = list(object_key_columns())
        key_col_indices = list(xrange(len(column_labels)))
        column_labels += ['Tracking Label','Timepoint ID',self.selected_measurement]
        group = 'Object'
        grid = tableviewer.TableViewer(self, title='Data table from trajectory %d containing %s %s'%(self.selected_trajectory[0],props.object_name[0],self.selected_node))
        grid.table_from_array(table_data, column_labels, group, key_col_indices)
        # Sort by label first, then by timepoint
        grid.grid.Table.set_sort_col(len(key_col_indices)+1)
        grid.grid.Table.set_sort_col(len(key_col_indices)+2,add=True) 
        # Hide the object key columns
        grid.grid.Table.set_shown_columns(list(xrange(len(key_col_indices),len(column_labels))))
        grid.grid.Table.ResetView(grid.grid)
        grid.set_fitted_col_widths()
        grid.Show()
        
    def select_point1_for_montage(self,event=None):
        self.select_point_for_montage(1)
        
    def select_point2_for_montage(self,event=None):
        self.select_point_for_montage(2)        
    
    def select_point_for_montage(self, num):
        idx = num-1
        self.selected_endpoints[idx] = self.selected_node
        self.trajectory_point_selection_outline[idx].mlab_source.dataset.points[0] = (np.mean(self.trajectory_selection_outline.bounds[:2]),
                                                                                         np.mean(self.trajectory_selection_outline.bounds[2:4]),
                                                                                         np.mean(self.trajectory_selection_outline.bounds[4:]))
        self.trajectory_point_selection_outline[idx].actor.actor.visibility = 1
        
        self.lineage_point_selection_outline[idx].mlab_source.dataset.points[0] = (np.mean(self.lineage_selection_outline.bounds[:2]),
                                                                                      np.mean(self.lineage_selection_outline.bounds[2:4]),
                                                                                      0)
        self.lineage_point_selection_outline[idx].actor.actor.visibility = 1  
    
    def show_cell_montage(self, event = None):
        # Pick out the trajectory containing the selected endpoints
        # TODO: Probably should check whether the endpoints lie in the same trajectory when selecting them
        trajectory_to_use = np.unique([key for key in self.connected_nodes.keys() for endpoint in self.selected_endpoints if endpoint in self.connected_nodes[key]]).tolist()
        if len(trajectory_to_use) > 1:
            print "Should have one trajectory selected"
            return
        else:
            trajectory_to_use = trajectory_to_use[0]
            
        # Check the node ordering
        selected_endpoints = self.selected_endpoints if nx.has_path(self.directed_graph, self.selected_endpoints[0],self.selected_endpoints[1]) else self.selected_endpoints[::-1]
            
        current_trajectory_keys = nx.shortest_path(self.connected_nodes[trajectory_to_use], selected_endpoints[0],selected_endpoints[1])
        montage_frame = sortbin.CellMontageFrame(get_main_frame_or_none(),"Image montage from trajectory %d containing %s %s and %s"%(trajectory_to_use, props.object_name[0],selected_endpoints[0],selected_endpoints[1] ))
        montage_frame.Show()
        montage_frame.add_objects(current_trajectory_keys)
        [tile.Select() for tile in montage_frame.sb.tiles if tile.obKey in self.selected_endpoints]
    
    def show_cell_tile(self, event = None):
        montage_frame = sortbin.CellMontageFrame(get_main_frame_or_none(),"Image tile of %s %s"%(props.object_name[0],self.selected_node))
        montage_frame.Show()
        montage_frame.add_objects([self.selected_node])   
    
    def show_full_image(self, event = None):
        imViewer = imagetools.ShowImage(self.selected_node, props.image_channel_colors, parent=self)
        imViewer.imagePanel.SelectPoint(db.GetObjectCoords(self.selected_node))
    
    def on_dataset_selected(self, event = None):
        # Disable trajectory selection button until plot updated or the currently plotted dataset is selected
        self.do_plots_need_updating["dataset"] = False
        if self.selected_dataset == self.control_panel.dataset_choice.GetStringSelection():
            self.control_panel.trajectory_selection_button.Enable()
        else:
            self.control_panel.trajectory_selection_button.Disable()
            self.selected_dataset = self.control_panel.dataset_choice.GetStringSelection()
            self.do_plots_need_updating["dataset"] = True
            
    def on_measurement_selected(self, event = None):
        self.do_plots_need_updating["measurement"] = False
        if self.selected_measurement == self.control_panel.measurement_choice.GetStringSelection():
            self.control_panel.trajectory_selection_button.Enable()
        else:
            self.selected_measurement = self.control_panel.measurement_choice.GetStringSelection()            
            self.control_panel.trajectory_selection_button.Disable()  
            self.do_plots_need_updating["measurement"] = True

    def on_colormap_selected(self, event = None):
        self.do_plots_need_updating["colormap"] = False
        if self.selected_colormap != self.control_panel.colormap_choice.GetStringSelection():
            self.selected_colormap = self.control_panel.colormap_choice.GetStringSelection()    
            self.do_plots_need_updating["colormap"] = True
    
    #def on_filter_selected(self, event = None):
        #self.do_plots_need_updating["filter"] = []
        #for current_filter in self.control_panel.filter_panel.filters:
            #self.do_plots_need_updating["filter"].append(" ".join((current_filter.colChoice.GetStringSelection(), 
                                                                   #current_filter.comparatorChoice.GetStringSelection(),
                                                                   #current_filter.valueField.GetStringSelection())))
            
    def calculate_and_display_lap_stats(self, event = None):
        self.show_LAP_metrics()
    
    def update_trajectory_selection(self, event = None):
        
        class TrajectoryMultiChoiceDialog (wx.Dialog):
            '''
            Build the dialog box that appears when you click on the trajectory selection
            '''
            def __init__(self, parent, message="", caption="", choices=[]):
                wx.Dialog.__init__(self, parent, -1)
                self.SetTitle(caption)
                sizer1 = wx.BoxSizer(wx.VERTICAL)
                self.message = wx.StaticText(self, -1, message)
                self.clb = wx.CheckListBox(self, -1, choices = choices)
                self.selectallbtn = wx.Button(self,-1,"Select all")
                self.deselectallbtn = wx.Button(self,-1,"Deselect all")
                sizer2 = wx.BoxSizer(wx.HORIZONTAL)
                sizer2.Add(self.selectallbtn,0, wx.ALL | wx.EXPAND, 5)
                sizer2.Add(self.deselectallbtn,0, wx.ALL | wx.EXPAND, 5)
                self.dlgbtns = self.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL)
                self.Bind(wx.EVT_BUTTON, self.SelectAll, self.selectallbtn)
                self.Bind(wx.EVT_BUTTON, self.DeselectAll, self.deselectallbtn)
                
                sizer1.Add(self.message, 0, wx.ALL | wx.EXPAND, 5)
                sizer1.Add(self.clb, 1, wx.ALL | wx.EXPAND, 5)
                sizer1.Add(sizer2, 0, wx.EXPAND)
                sizer1.Add(self.dlgbtns, 0, wx.ALL | wx.EXPAND, 5)
                self.SetSizer(sizer1)
                self.Fit()
                
            def GetSelections(self):
                return self.clb.GetChecked()
            
            def SetSelections(self, indexes):
                return self.clb.SetChecked(indexes)

            def SelectAll(self, event):
                for i in range(self.clb.GetCount()):
                    self.clb.Check(i, True)
                    
            def DeselectAll(self, event):
                for i in range(self.clb.GetCount()):
                    self.clb.Check(i, False)
        
        trajectory_selection_dlg = TrajectoryMultiChoiceDialog(self, 
                                                    message = 'Select the objects you would like to show',
                                                    caption = 'Select trajectories to visualize', 
                                                    choices = [str(x) for x in self.connected_nodes.keys()])
                
        current_selection = np.nonzero(self.trajectory_selection.values())[0]
        trajectory_selection_dlg.SetSelections(current_selection)
        
        if (trajectory_selection_dlg.ShowModal() == wx.ID_OK):
            current_selection = trajectory_selection_dlg.GetSelections()
            all_labels = self.connected_nodes.keys()
            self.trajectory_selection = dict.fromkeys(all_labels,0)
            for x in current_selection:
                self.trajectory_selection[all_labels[x]] = 1
            self.do_plots_need_updating["trajectories"] = True
            
            # Alter lines between the points that we have previously created by directly modifying the VTK dataset.                
            nodes_to_remove = [self.connected_nodes[key] for (key,value)in self.trajectory_selection.items() if value == 0]
            nodes_to_remove = [item for sublist in nodes_to_remove for item in sublist]
            mapping = dict(zip(sorted(self.directed_graph),range(0,self.directed_graph.number_of_nodes()+1)))
            nodes_to_remove = [mapping[item] for item in nodes_to_remove]
            self.altered_directed_graph = nx.relabel_nodes(self.directed_graph, mapping, copy=True)
            self.altered_directed_graph.remove_nodes_from(nodes_to_remove)
            self.update_plot()                    
    
    def enable_filtering(self, event=None):
        if self.control_panel.enable_filtering_checkbox.GetValue():
            self.control_panel.filter_panel.Enable()
        else:
            self.control_panel.filter_panel.Disable()
    
    def update_plot(self, event=None):
        self.do_plots_need_updating["filter"] = self.control_panel.enable_filtering_checkbox.IsChecked()   
        self.generate_graph()
        self.draw_trajectories()
        self.draw_lineage()
        self.control_panel.trajectory_selection_button.Enable()
        
        self.do_plots_need_updating["dataset"] = False
        self.do_plots_need_updating["colormap"] = False
        self.do_plots_need_updating["measurement"] = False
        self.do_plots_need_updating["trajectories"] = False
        
    def generate_graph(self):
        # Generate the graph relationship if the dataset has been updated
        
        if not self.do_plots_need_updating["filter"]:
            self.selected_filter = None
        else:
            self.selected_filter = []
            for current_filter in self.control_panel.filter_panel.filters:
                self.selected_filter.append(" ".join((props.object_table + "." + current_filter.colChoice.GetStringSelection(), 
                                                      current_filter.comparatorChoice.GetStringSelection(),
                                                      current_filter.valueField.GetValue())))
                
        column_names,trajectory_info = obtain_tracking_data(self.selected_dataset,
                                                            self.selected_measurement if self.selected_measurement in self.dataset_measurement_choices else None, 
                                                            self.selected_filter)
        
        if self.do_plots_need_updating["dataset"]:           
            logging.info("Retrieved %d %s from dataset %s"%(len(trajectory_info),props.object_name[1],self.selected_dataset))
            
            self.directed_graph = nx.DiGraph()
            key_length = len(object_key_columns())
            indices = range(0,key_length)
            node_ids = map(itemgetter(*indices),trajectory_info)
            indices = range(key_length,key_length+2)
            parent_node_ids = map(itemgetter(*indices),trajectory_info) 
            indices = range(key_length+2,len(trajectory_info[0]))
            attr = [dict(zip(track_attributes,item)) for item in map(itemgetter(*indices),trajectory_info)]
            # Add nodes
            self.directed_graph.add_nodes_from(zip(node_ids,attr))
            # Add edges as list of tuples (exclude those that have no parent, i.e, (0,0))
            null_node_id = (0,0)
            # TODO: Check if this is faster
            # z = np.array(zip(parent_node_ids,node_ids))
            # index = np.all(z[:,0] != np.array(null_node_id),axis=1)
            # z = z[index,:]
            # self.directed_graph.add_edges_from(zip([tuple(x) for x in z[:,0]],[tuple(x) for x in z[:,1]]))
            self.directed_graph.add_edges_from([(parent,node) for (node,parent) in zip(node_ids,parent_node_ids) if parent != null_node_id])
            
            logging.info("Constructed graph consisting of %d nodes and %d edges"%(self.directed_graph.number_of_nodes(),self.directed_graph.number_of_edges()))
            
            t1 = time.clock()
            G = nx.convert_node_labels_to_integers(self.directed_graph,
                                                   first_label=0,
                                                   ordering="default")
            mapping = dict(zip(G.nodes(),self.directed_graph.nodes()))
            glayout.layer_layout(G, level_attribute = "t")
            nx.relabel_nodes(G, mapping,copy=False) # Map back to original graph labels
            node_positions = dict(zip(G.nodes(),[[G.node[key]["t"],G.node[key]["y"]] for key in G.nodes()]))
            
            # Adjust the y-spacing between trajectories so it the plot is roughly square, to avoid nasty Mayavi axis scaling issues later
            # See: http://stackoverflow.com/questions/13015097/how-do-i-scale-the-x-and-y-axes-in-mayavi2
            xy = np.array([node_positions[key] for key in G.nodes()])
            scaling_y = 1.0/float(max(xy[:,1]) - min(xy[:,1]))*float(max(xy[:,0]) - min(xy[:,0]))
            for key in G.nodes(): node_positions[key][1] *= scaling_y
            
            t2 = time.clock()
            logging.info("Computed lineage layout (%.2f sec)"%(t2-t1))
            
            # Each track gets its own indexed subgraph. Later operations to the graphs are referenced to this key.
            connected_nodes = nx.connected_component_subgraphs(self.directed_graph.to_undirected())
            self.connected_nodes = dict(zip(range(1,len(connected_nodes)+1),connected_nodes))
            
            self.add_derived_measurements()
            
            # Insert derived measurements and update current selection
            measurement_choices = self.control_panel.dataset_measurement_choices + self.derived_measurements.keys()
            current_measurement_choice = self.control_panel.measurement_choice.GetSelection() 
            self.control_panel.measurement_choice.SetItems(measurement_choices)
            self.control_panel.measurement_choice.SetSelection(current_measurement_choice)
            
            self.lineage_node_positions = node_positions
            
            # When visualizing a new dataset, select all trajectories by default
            self.trajectory_selection = dict.fromkeys(self.connected_nodes.keys(),1)              
        else:
            key_length = len(object_key_columns())
            indices = range(0,key_length)
            if self.selected_measurement in self.dataset_measurement_choices:
                node_ids = map(itemgetter(*indices),trajectory_info)
                getitem = itemgetter(len(trajectory_info[0])-2) # Measurement values                
                attr = dict(zip(node_ids,[item for item in map(getitem,trajectory_info)]))        
            else:
                node_ids = sorted(self.directed_graph)
                attr = dict(zip(node_ids,self.derived_measurements[self.selected_measurement]))
            nx.set_node_attributes(self.directed_graph,"s",attr)
            getitem = itemgetter(len(trajectory_info[0])-1) # Filter values
            attr = dict(zip(node_ids,[item for item in map(getitem,trajectory_info)])) 
            nx.set_node_attributes(self.directed_graph,"f",attr)
            
        self.scalar_data = np.array([self.directed_graph.node[key]["s"] for key in sorted(self.directed_graph)]).astype(float)

    def add_derived_measurements(self):
        logging.info("Calculating derived measurements")
                    
        t1 = time.clock()   
        # TODO: Allow for user choice to add derived measurements
        # Create dict for QC measurements derived from graph properities
        self.derived_measurements = {}      
        
        # Find start/end nodes by checking for nodes with no outgoing/ingoing edges
        start_nodes = [node for (node,value) in self.directed_graph.in_degree().items() if value == 0]
        end_nodes = [node for (node,value) in self.directed_graph.out_degree().items() if value == 0]
        self.start_nodes = dict([(key,node) for (key,value) in self.connected_nodes.items() for node in start_nodes if node in value ])
        self.end_nodes = dict([(key,node) for (key,value) in self.connected_nodes.items() for node in end_nodes if node in value ])
        max_timepoint = max([item[0] for item in end_nodes])
        self.terminal_nodes = [item for item in end_nodes if item[0] == max_timepoint]
        
        # Find branchpoints and nodes with a distance threshold from them (for later pruning if desired)
        branch_node_list = [node for (node,value) in self.directed_graph.out_degree().items() if value > 1]
        subgraph_branch_nodes = [(key,node) for (key,value) in self.connected_nodes.items() for node in branch_node_list if node in value ]
        self.branch_nodes = {k: [] for k in self.connected_nodes.keys()}
        [self.branch_nodes[key].append(value) for (key,value) in subgraph_branch_nodes]
        cutoff_dist_from_branch = 4 # TODO: Allow for user-selected distance cutoff
        end_nodes_for_pruning = set()
        for source_node in branch_node_list:
            # Find out-degrees for all nodes within N nodes of branchpoint
            out_degrees = self.directed_graph.out_degree(nx.single_source_shortest_path_length(self.directed_graph,
                                                                                               source_node,
                                                                                               cutoff_dist_from_branch).keys())
            # Find all nodes for which the out-degree is 0 (i.e, all terminal nodes (leaves)) and not at end of movie
            branch_to_leaf_endpoints = [(source_node,path_node) for (path_node,degree) in out_degrees.items() if degree == 0 and path_node not in self.terminal_nodes]
            if len(branch_to_leaf_endpoints) > 0:
                for current_branch in branch_to_leaf_endpoints:
                    shortest_path = nx.shortest_path(self.directed_graph,current_branch[0],current_branch[1]) 
                    shortest_path.remove(source_node) # Remove the intital branchpoint
                    # Skip this path if another branchpoint exists, since it will get caught later
                    if all(np.array(self.directed_graph.out_degree(shortest_path).values()) <= 1): 
                        # Add nodes on the path from the branchpoint to the leaf
                        end_nodes_for_pruning.update(shortest_path)
        # TODO: This page (http://stackoverflow.com/questions/740287/python-check-if-one-of-the-following-items-is-in-a-list)
        #  indicates that using numpy might be faster than the set approach below. Check to confirm.
        # end_nodes_for_pruning = list(end_nodes_for_pruning.difference(set(branch_node_list)))
        branch_node_list = np.array(branch_node_list,dtype=[('i',int),('j',int)])
        end_nodes_for_pruning = np.array(list(end_nodes_for_pruning),dtype=[('i',int),('j',int)])
        end_nodes_for_pruning = list(end_nodes_for_pruning[-np.in1d(end_nodes_for_pruning,branch_node_list)])
        end_nodes_for_pruning = [tuple(item) for item in end_nodes_for_pruning] # Convert numpy.void elements back to tuples
        self.derived_measurements["NodesWithinDistanceCutoff"] = np.array([key in end_nodes_for_pruning for key in sorted(self.directed_graph) ]).astype(float)
        
        singletons = set(start_nodes).intersection(set(end_nodes))
        self.derived_measurements["Singletons"] = np.array([key in singletons for key in sorted(self.directed_graph) ]).astype(float)
        
        t2 = time.clock()
        logging.info("Computed derived measurements (%.2f sec)"%(t2-t1))        
        
    def find_fig(self, parent=None, title="", name=wx.FrameNameStr, subplots=None):
        """Find a figure frame window. Returns the window or None"""
        if parent:
            window = parent.FindWindowByName(name)
            if window:
                if len(title) and title != window.Title:
                    window.Title = title
                #window.set_subplots(subplots)
                window.figure.clf()
            return window    

    def create_or_find(self, parent=None, id=-1, title="", 
                       pos=wx.DefaultPosition, size=wx.DefaultSize,
                       style=wx.DEFAULT_FRAME_STYLE, name=wx.FrameNameStr,
                       subplots=None,
                       on_close=None):
        """Create or find a figure frame window"""
        win = self.find_fig(parent, title, name, subplots)
        return win or FigureFrame(parent, id, title, pos, size, style, name, 
                                    subplots, on_close)    
    def show_LAP_metrics(self):
        # See http://cshprotocols.cshlp.org/content/2009/12/pdb.top65.full, esp. Figure 5
        # Create new figure
        new_title = "LAP diagnostic metrics"
        window = self.create_or_find(self, -1, new_title, subplots=(2,1), name=new_title)
        
        # Plot the frame-to-frame linking distances
        dists = self.calculate_frame_to_frame_linking_distances() 
        title = "Frame-to-frame linking distances"
        axes = window.figure.add_subplot(2,2,1)   
        
        import matplotlib.pylab as plt
        axes = window.figure.add_subplot(2,2,1) 
        axes.set_axis_off()
        axes.text(0.0,0.0,
                  "Frame-to-frame linking distances\n"
                  "are the distances between the\n"
                  "predicted position of an object\n"
                  "and the observed position. This\n"
                  "data is displayed as a histogram\n"
                  "and should decay to zero.\n\n"
                  "The arrow indicates the pixel\n"
                  "distance at which 95%% of the\n"
                  "maximum number of links were\n"
                  "made. You should confirm that\n"
                  "this value is less than the\n"
                  "maximum search radius in the\n"
                  "%s module."%TRACKING_MODULE_NAME)
        axes = window.figure.add_subplot(2,2,2)        
        if dists.shape[0] == 0:
            plot = axes.text(0.0, 1.0, "No valid values to plot.")
            axes.set_axis_off()  
        else:
            bins = np.arange(0, np.max(dists))
            n, _, _ = axes.hist(dists, bins,
                          edgecolor='none',
                          alpha=0.75)
            max_search_radius = bins[n < 0.05*np.max(n)][0]
            axes.annotate('95%% of max count: %d pixels'%(max_search_radius),
                          xy=(max_search_radius,n[n < 0.05*np.max(n)][0]), 
                          xytext=(max_search_radius,axes.get_ylim()[1]/2), 
                          arrowprops=dict(facecolor='red', shrink=0.05))
            axes.set_xlabel('Frame-to-frame linking distances (pixels)')
            #axes.set_xlim((0,np.mean(dists) + 2*np.std(dists)))
            axes.set_ylabel('Counts')        
        
        # Plot the gap lengths
        axes = window.figure.add_subplot(2,2,3) 
        axes.set_axis_off()
        axes.text(0.0,0.0,
                  "Gap lengths are displayed as\n"
                  "a histogram. A plateau in the\n"
                  "tail of the histogram \n"
                  "indicates that the time window\n"
                  "used for gap closing is too \n"
                  "large, resulting in falsely \n"
                  "closed gaps.\n\n"
                  "If all the gap lengths are 1,\n"
                  "no data is shown since gap\n"
                  "closing was not necessary.")        
        values = np.array(self.calculate_gap_lengths()).flatten()
        values = values[np.isfinite(values)] # Just in case
        title = "Gap lengths"
        axes = window.figure.add_subplot(2,2,4)        
        if values.shape[0] == 0:
            plot = axes.text(0.1, 0.5, "No valid values to plot.")
            axes.set_axis_off()  
        elif np.max(values) == 1:
            plot = axes.text(0.1, 0.5, "No gap lengths > 1")
            axes.set_axis_off()              
        else:
            bins = np.arange(1, np.max(values)+1)
            axes.hist(values, bins,
                      facecolor=(0.0, 0.62, 1.0),
                      edgecolor='none',
                      alpha=0.75)
            axes.set_xlabel('Gap length (frames)')
            axes.set_ylabel('Counts')
        
        # Draw the figure
        window.figure.canvas.draw()
        
    def where_stmt_for_tracked_object(self,obj, group_id, selected_dataset):
        # We want relationships that are cover the following:
        # - Are parent/child, e.g, exclude neighborhood
        # - Share the same parent/child object, e.g, exclude primary/secondary/tertiary, some cases of neighborhood
        # - Are across-frame, e.g, exclude neighborhood, primary/secondary/tertiary
        # - For the selected dataset        
        stmt = ("LOWER(relationship) = 'parent'", 
                "AND object_name1 = '%s'"%(obj),
                "AND object_name1 = object_name2",
                "AND image_number1 != image_number2",
                "AND i1.%s = %d"%(group_id,selected_dataset)
                )
        return stmt
    
    def calculate_gap_lengths(self):
        obj = props.cell_x_loc.split('_Location_Center')[0]
        
        query = ("SELECT",
                 "ABS(i2.%s - i1.%s)"%(props.timepoint_id, props.timepoint_id),
                 "FROM %s r"%(props.relationships_view),  
                 "JOIN %s i1"%(props.image_table),
                 "ON r.image_number1 = i1.%s"%(props.image_id),
                 "JOIN %s i2"%(props.image_table),
                 "ON r.image_number2 = i2.%s"%(props.image_id),
                 "WHERE") + \
                 self.where_stmt_for_tracked_object(obj,props.group_id,int(self.selected_dataset)) 
        query = " ".join(query)
        return(np.array([_ for _ in db.execute(query)]))
    
    def calculate_frame_to_frame_linking_distances(self):
        #What's recorded in the database for each object are the values necessary to predict the 
        # location of the object in the next frame. There are two models applicable in TrackObjects: Random (NoVel) and Velocity (Vel)
        # For the Velocity model, you have to add Kalman_State_Vel_X and Kalman_State_Vel_VX and similarly for Y to get the 
        # predicted (X,Y) position. 
        # For NoVel, it's Kalman_State_NoVel_X and Kalman_State_NoVel_Y that gives the predicted (X,Y) position.
        # The error in distance between the observed location and the predicted location is calculated depending on which 
        # the user picked in TrackObjects (usually both) and the appropriate model is picked on a per-object basis as the
        # minimum of the two.
        obj = props.cell_x_loc.split('_Location_Center')[0]
        prefix = "_".join((obj,TRACKING_MODULE_NAME))
        if props.db_type == 'sqlite':
            query = "PRAGMA table_info(%s)"%(props.object_table)
            used_velocity_model = len([item[1] for item in db.execute(query) if item[1].find('_Kalman_Vel_') > 0]) > 0    
            used_novelocity_model = len([item[1] for item in db.execute(query) if item[1].find('_Kalman_NoVel_') > 0]) > 0  
        else:
            query = "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = '%s' AND COLUMN_NAME REGEXP '_Kalman_Vel_'"%(props.db_name, props.object_table)
            used_velocity_model = db.execute(query)[0][0] > 0
            query = "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = '%s' AND TABLE_NAME = '%s' AND COLUMN_NAME REGEXP '_Kalman_NoVel_'"%(props.db_name, props.object_table)
            used_novelocity_model = db.execute(query)[0][0] > 0
        # SQL courtesy of Lee. 
        query = ("SELECT",
                 "pred.pred_vel_x, pred.pred_vel_y, pred.pred_novel_x, pred.pred_novel_y, obs.%s_Location_Center_X, obs.%s_Location_Center_Y"%(obj,obj),
                 "FROM %s r"%(props.relationships_view),
                 "JOIN",
                 "(SELECT %s AS ImageNumber, %s AS ObjectNumber,"%(props.image_id, props.object_id),
                 "(%s_Kalman_Vel_State_X + %s_Kalman_Vel_State_VX)"%(prefix,prefix) if used_velocity_model else "NULL","AS pred_vel_x,",
                 "(%s_Kalman_Vel_State_Y + %s_Kalman_Vel_State_VY)"%(prefix,prefix) if used_velocity_model else "NULL","AS pred_vel_y,",
                 "%s_Kalman_NoVel_State_X AS pred_novel_x, %s_Kalman_NoVel_State_Y"%(prefix,prefix) if used_novelocity_model else "NULL","AS pred_novel_y",
                 "FROM %s) AS pred ON r.image_number1 = pred.ImageNumber AND r.object_number1 = pred.ObjectNumber"%(props.object_table),
                 "JOIN %s AS obs ON r.image_number2 = obs.%s AND r.object_number2 = obs.%s"%(props.object_table, props.image_id, props.object_id),
                 "JOIN %s AS i1 ON r.image_number1 = i1.%s"%(props.image_table, props.image_id),
                 "JOIN %s AS i2 ON r.image_number2 = i2.%s"%(props.image_table, props.image_id),
                 "WHERE i1.%s + 1 = i2.%s"%(props.timepoint_id,props.timepoint_id),
                 "AND i1.%s = i2.%s"%(props.group_id,props.group_id),
                 "AND") + \
            self.where_stmt_for_tracked_object(obj,props.group_id,int(self.selected_dataset)) 
        query = " ".join(query)
        values = np.array(db.execute(query))
        # I would love to do this arithmetic in the query, but SQLite doesn't handle SQRT or POW
        dist_error_novel = np.sqrt((values[:,2]-values[:,4])**2 + (values[:,3]-values[:,5])**2)
        dist_error_vel = np.sqrt((values[:,0]-values[:,4])**2 + (values[:,1]-values[:,5])**2)
        # Return the minimum of the two models for each object/timepoint
        return(np.min(np.vstack((dist_error_novel,dist_error_vel)),axis=0))
        
    def on_pick_one_timepoint(self,picker):
        """ Picker callback: this gets called upon pick events.
        """
        # Retrieving the data from Mayavi pipelines: http://docs.enthought.com/mayavi/mayavi/data.html#retrieving-the-data-from-mayavi-pipelines
        # More helpful example: http://docs.enthought.com/mayavi/mayavi/auto/example_select_red_balls.html
        if picker.actor in self.lineage_node_collection.actor.actors + self.lineage_edge_collection.actor.actors:
            # TODO: Figure what the difference is between node_collection and edge_collection being clicked on.
            # Retrieve to which point corresponds the picked point. 
            # Here, we grab the points describing the individual glyph, to figure
            # out how many points are in an individual glyph.                
            n_glyph = self.lineage_node_collection.glyph.glyph_source.glyph_source.output.points.to_array().shape[0]
            # Find which data point corresponds to the point picked:
            # we have to account for the fact that each data point is
            # represented by a glyph with several points      
            point_id = picker.point_id/n_glyph
            x_lineage,y_lineage,_ = self.lineage_node_collection.mlab_source.points[point_id,:]
            x_traj,y_traj,t_traj = self.trajectory_node_collection.mlab_source.points[point_id,:]
            picked_node = sorted(self.directed_graph)[point_id]
                
        elif picker.actor in self.trajectory_node_collection.actor.actors:
            n_glyph = self.trajectory_node_collection.glyph.glyph_source.glyph_source.output.points.to_array().shape[0]  
            point_id = picker.point_id/n_glyph            
            x_traj,y_traj,t_traj = self.trajectory_node_collection.mlab_source.points[point_id,:]
            x_lineage,y_lineage,_ = self.lineage_node_collection.mlab_source.points[point_id,:]
            picked_node = sorted(self.directed_graph)[point_id]
        else:
            picked_node = None

        if picked_node != None:
            # If the picked node is not one of the selected trajectories, then don't select it 
            if picked_node == self.selected_node:
                self.selected_node = None
                self.selected_trajectory = None      
                self.lineage_selection_outline.actor.actor.visibility = 0
                self.trajectory_selection_outline.actor.actor.visibility = 0
            else:
                self.selected_node = picked_node
                self.selected_trajectory = [key for key in self.connected_nodes.keys() if self.selected_node in self.connected_nodes[key]]
                
                # Move the outline to the data point
                dx = np.diff(self.lineage_selection_outline.bounds[:2])[0]/2
                dy = np.diff(self.lineage_selection_outline.bounds[2:4])[0]/2           
                self.lineage_selection_outline.bounds = (x_lineage-dx, x_lineage+dx,
                                                         y_lineage-dy, y_lineage+dy,
                                                         0, 0)
                self.lineage_selection_outline.actor.actor.visibility = 1
                
                dx = np.diff(self.trajectory_selection_outline.bounds[:2])[0]/2
                dy = np.diff(self.trajectory_selection_outline.bounds[2:4])[0]/2
                dt = np.diff(self.trajectory_selection_outline.bounds[4:6])[0]/2
                self.trajectory_selection_outline.bounds = (x_traj-dx, x_traj+dx,
                                                            y_traj-dy, y_traj+dy,
                                                            t_traj-dt, t_traj+dt)
                self.trajectory_selection_outline.actor.actor.visibility = 1
                  
    def draw_lineage(self):
        # Rendering temporarily disabled
        self.mayavi_view.lineage_scene.disable_render = True 

        # (Possibly) Helpful pages on using NetworkX and Mayavi:
        # http://docs.enthought.com/mayavi/mayavi/auto/example_delaunay_graph.html
        # https://groups.google.com/forum/?fromgroups=#!topic/networkx-discuss/wdhYIPeuilo
        # http://www.mail-archive.com/mayavi-users@lists.sourceforge.net/msg00727.html        

        # Draw the lineage tree if the dataset has been updated
        if self.do_plots_need_updating["dataset"]:
            # Clear the scene
            logging.info("Drawing lineage graph...")
            self.mayavi_view.lineage_scene.mlab.clf(figure = self.mayavi_view.lineage_scene.mayavi_scene)
             
            #mlab.title("Lineage tree",size=2.0,figure=self.mayavi_view.lineage_scene.mayavi_scene)   
            
            t1 = time.clock()
            
            G = nx.convert_node_labels_to_integers(self.directed_graph,ordering="sorted")
            xys = np.array([self.lineage_node_positions[node]+[self.directed_graph.node[node]["s"]] for node in sorted(self.directed_graph.nodes()) ])
            #xys = np.array([self.lineage_node_positions[node]+[self.directed_graph.node[node]["s"]] for node in sorted(self.directed_graph.nodes()) if self.directed_graph.node[node]["f"]==0])
            #if len(xys) == 0:
                #xys = np.array(3*[np.NaN],ndmin=2)
            dt = np.median(np.diff(np.unique(nx.get_node_attributes(self.directed_graph,"t").values())))
            # The scale factor defaults to the typical interpoint distance, which may not be appropriate. 
            # So I set it explicitly here to a fraction of delta_t
            # To inspect the value, see pts.glyph.glpyh.scale_factor
            node_scale_factor = 0.5*dt
            pts = mlab.points3d(xys[:,0], xys[:,1], np.zeros_like(xys[:,0]), xys[:,2],
                                scale_factor = node_scale_factor, 
                                scale_mode = 'none',
                                colormap = self.selected_colormap,
                                resolution = 8,
                                figure = self.mayavi_view.lineage_scene.mayavi_scene) 
            pts.glyph.color_mode = 'color_by_scalar'
            pts.mlab_source.dataset.lines = np.array(G.edges())

            self.lineage_node_collection = pts
            
            tube_radius = node_scale_factor/10.0
            tube = mlab.pipeline.tube(pts, 
                                      tube_radius = tube_radius, # Default tube_radius results in v. thin lines: tube.filter.radius = 0.05
                                      figure = self.mayavi_view.lineage_scene.mayavi_scene)
            self.lineage_edge_collection = mlab.pipeline.surface(tube, 
                                                                 color=(0.8, 0.8, 0.8),
                                                                 figure = self.mayavi_view.lineage_scene.mayavi_scene)
            
            # Add object label text to the left
            text_scale_factor = node_scale_factor/1.0 
            self.lineage_label_collection = dict(zip(self.connected_nodes.keys(),
                                                     [mlab.text3d(self.lineage_node_positions[self.start_nodes[key]][0]-0.75*dt,
                                                                  self.lineage_node_positions[self.start_nodes[key]][1],
                                                                  0,
                                                                  str(key),
                                                                  scale = text_scale_factor,
                                                                  figure = self.mayavi_view.lineage_scene.mayavi_scene)
                                                      for key in self.connected_nodes.keys()]))

            # Add outline to be used later when selecting points
            self.lineage_selection_outline = mlab.outline(line_width=3,
                                                          figure = self.mayavi_view.lineage_scene.mayavi_scene)
            self.lineage_selection_outline.outline_mode = 'cornered'
            self.lineage_selection_outline.actor.actor.visibility = 0
            self.lineage_selection_outline.bounds = (-node_scale_factor,node_scale_factor,
                                                     -node_scale_factor,node_scale_factor,
                                                     -node_scale_factor,node_scale_factor)            
            # Add 2 more outlines to be used later when selecting points
            self.lineage_point_selection_outline = []
            for i in range(2):
                ol = mlab.points3d(0,0,0,
                                   extent = list(2*np.array([-node_scale_factor,node_scale_factor,
                                             -node_scale_factor,node_scale_factor,
                                             -node_scale_factor,node_scale_factor])),
                                   color = (1,0,1),
                                   mode = 'sphere',
                                   scale_factor = 2*node_scale_factor, 
                                   scale_mode = 'none',                                   
                                   figure = self.mayavi_view.trajectory_scene.mayavi_scene)
                ol.actor.actor.visibility = 0    
                self.lineage_point_selection_outline.append(ol)            

            # Add axes outlines
            extent = np.array(self.lineage_node_positions.values())
            extent = (0,np.max(extent[:,0]),0,np.max(extent[:,1]),0,0)
            mlab.pipeline.outline(self.lineage_node_collection,
                                  extent = extent,
                                  opacity = self.axes_opacity,
                                  figure = self.mayavi_view.lineage_scene.mayavi_scene) 
            mlab.axes(self.lineage_node_collection, 
                      xlabel='T', ylabel='',
                      extent = extent,
                      opacity = self.axes_opacity,
                      x_axis_visibility=True, y_axis_visibility=False, z_axis_visibility=False)             
            self.mayavi_view.lineage_scene.reset_zoom()
            
            # Constrain view to 2D
            self.mayavi_view.lineage_scene.interactor.interactor_style = tvtk.InteractorStyleImage()

            # Make the graph clickable
            #super(MayaviView, self).update_plot()
            self.mayavi_view.lineage_scene.mayavi_scene.on_mouse_pick(self.on_pick_one_timepoint,
                                                                      type='point',
                                                                      button='Left',
                                                                      remove=False)          
            t2 = time.clock()
            logging.info("Computed layout (%.2f sec)"%(t2-t1))   
        else:
            logging.info("Re-drawing lineage tree...")
            
            if self.do_plots_need_updating["trajectories"]:
                self.lineage_node_collection.mlab_source.dataset.lines = np.array(self.altered_directed_graph.edges())
                self.lineage_node_collection.mlab_source.update()
                #self.lineage_edge_collection.mlab_source.dataset.lines = np.array(self.altered_directed_graph.edges())
                #self.lineage_edge_collection.mlab_source.update()
                
                for key in self.connected_nodes.keys():
                    self.lineage_label_collection[key].actor.actor.visibility = self.trajectory_selection[key]

            if self.do_plots_need_updating["measurement"]:
                self.lineage_node_collection.mlab_source.set(scalars = self.scalar_data)
            
            if self.do_plots_need_updating["colormap"]:
                # http://docs.enthought.com/mayavi/mayavi/auto/example_custom_colormap.html
                self.lineage_node_collection.module_manager.scalar_lut_manager.lut_mode = self.selected_colormap
                
        # Re-enable the rendering
        self.mayavi_view.lineage_scene.disable_render = False

    def draw_trajectories(self):
        # Rendering temporarily disabled
        self.mayavi_view.trajectory_scene.disable_render = True  
        
        # Draw the lineage tree if either (1) all the controls indicate that updating is needed (e.g., initial condition) or
        # (2) if the dataset has been updated        
        if self.do_plots_need_updating["dataset"]:

            logging.info("Drawing trajectories...")
            # Clear the scene
            self.mayavi_view.trajectory_scene.mlab.clf(figure = self.mayavi_view.trajectory_scene.mayavi_scene)
    
            #mlab.title("Trajectory plot",size=2.0,figure=self.mayavi_view.trajectory_scene.mayavi_scene)   
    
            t1 = time.clock()
            
            G = nx.convert_node_labels_to_integers(self.directed_graph,ordering="sorted")
    
            xyts = np.array([(self.directed_graph.node[key]["x"],
                              self.directed_graph.node[key]["y"],
                              self.directed_graph.node[key]["t"],
                              self.directed_graph.node[key]["s"]) for key in sorted(self.directed_graph)])
            
            # Compute reasonable scaling factor according to the data limits.
            # We want the plot to be roughly square, to avoid nasty Mayavi axis scaling issues later.
            # Unfortunately, adjusting the surface.actor.actor.scale seems to lead to more problems than solutions.
            # See: http://stackoverflow.com/questions/13015097/how-do-i-scale-the-x-and-y-axes-in-mayavi2
            t_scaling = np.mean( [(max(xyts[:,0])-min(xyts[:,0])), (max(xyts[:,1])-min(xyts[:,1]))] ) / (max(xyts[:,2])-min(xyts[:,2]))
            xyts[:,2] *= t_scaling
    
            # Taken from http://docs.enthought.com/mayavi/mayavi/auto/example_plotting_many_lines.html
            # Create the lines
            self.trajectory_line_source = mlab.pipeline.scalar_scatter(xyts[:,0], xyts[:,1], xyts[:,2], xyts[:,3], \
                                                                       figure = self.mayavi_view.trajectory_scene.mayavi_scene)
            # Connect them using the graph edge matrix
            self.trajectory_line_source.mlab_source.dataset.lines = np.array(G.edges())     
            
            # Finally, display the set of lines by using the surface module. Using a wireframe
            # representation allows to control the line-width.
            self.trajectory_line_collection = mlab.pipeline.surface(mlab.pipeline.stripper(self.trajectory_line_source), # The stripper filter cleans up connected lines; it regularizes surfaces by creating triangle strips
                                                                    line_width=1, 
                                                                    colormap=self.selected_colormap,
                                                                    figure = self.mayavi_view.trajectory_scene.mayavi_scene)         
    
            # Generate the corresponding set of nodes
            dt = np.median(np.diff(np.unique(nx.get_node_attributes(self.directed_graph,"t").values())))
            # Try to scale the nodes in a reasonable way
            # To inspect, see pts.glyph.glpyh.scale_factor 
            node_scale_factor = 0.5*dt
            pts = mlab.points3d(xyts[:,0], xyts[:,1], xyts[:,2], xyts[:,3],
                                scale_factor = 0.0,
                                scale_mode = 'none',
                                colormap = self.selected_colormap,
                                figure = self.mayavi_view.trajectory_scene.mayavi_scene) 
            pts.glyph.color_mode = 'color_by_scalar'
            pts.mlab_source.dataset.lines = np.array(G.edges())
            self.trajectory_node_collection = pts    
    
            # Add object label text at end of trajectory
            text_scale_factor = node_scale_factor*5 
            self.trajectory_label_collection = dict(zip(self.connected_nodes.keys(),
                                                        [mlab.text3d(self.directed_graph.node[sorted(subgraph)[-1]]["x"],
                                                                     self.directed_graph.node[sorted(subgraph)[-1]]["y"],
                                                                     self.directed_graph.node[sorted(subgraph)[-1]]["t"]*t_scaling,
                                                                     str(key),
                                                                     scale = text_scale_factor,
                                                                     name = str(key),
                                                                     figure = self.mayavi_view.trajectory_scene.mayavi_scene) 
                                                         for (key,subgraph) in self.connected_nodes.items()]))
            
            # Add outline to be used later when selecting points
            self.trajectory_selection_outline = mlab.outline(line_width = 3,
                                                             figure = self.mayavi_view.trajectory_scene.mayavi_scene)
            self.trajectory_selection_outline.outline_mode = 'cornered'
            self.trajectory_selection_outline.bounds = (-node_scale_factor,node_scale_factor,
                                                        -node_scale_factor,node_scale_factor,
                                                        -node_scale_factor,node_scale_factor)
            self.trajectory_selection_outline.actor.actor.visibility = 0
            
            # Add 2 more points to be used later when selecting points
            self.trajectory_point_selection_outline = []
            for i in range(2):
                ol = mlab.points3d(0,0,0,
                                   extent = [-node_scale_factor,node_scale_factor,
                                             -node_scale_factor,node_scale_factor,
                                             -node_scale_factor,node_scale_factor],
                                   color = (1,0,1),
                                   figure = self.mayavi_view.trajectory_scene.mayavi_scene)
                ol.actor.actor.visibility = 0    
                self.trajectory_point_selection_outline.append(ol)
            
            # Using axes doesn't work until the scene is avilable: 
            # http://docs.enthought.com/mayavi/mayavi/building_applications.html#making-the-visualization-live
            mlab.pipeline.outline(self.trajectory_line_source,
                                  opacity = self.axes_opacity,
                                  figure = self.mayavi_view.trajectory_scene.mayavi_scene) 
            mlab.axes(self.trajectory_line_source, 
                      xlabel='X', ylabel='Y',zlabel='T',
                      opacity = self.axes_opacity,
                      x_axis_visibility=True, y_axis_visibility=True, z_axis_visibility=True)
            
            # Set axes to MATLAB's default 3d view
            mlab.view(azimuth = 322.5,elevation = 30.0,
                      figure = self.mayavi_view.trajectory_scene.mayavi_scene)
            self.mayavi_view.trajectory_scene.reset_zoom()
            
            # An trajectory picker object is created to trigger an event when a trajectory is picked. 
            # Can press 'p' to get UI on curretn pick
            # TODO: Figure out how to re-activate picker on scene refresh
            #  E.g., (not identical problem) http://www.mail-archive.com/mayavi-users@lists.sourceforge.net/msg00583.html
            picker = self.mayavi_view.trajectory_scene.mayavi_scene.on_mouse_pick(self.on_pick_one_timepoint,
                                                                                  type='point',
                                                                                  button='Left',
                                                                                  remove=False)
            picker.tolerance = 0.01
            
            # Figure decorations
            # Orientation axes
            #mlab.orientation_axes(zlabel = "T", 
                                  #line_width = 5,
                                  #figure = self.mayavi_view.trajectory_scene.mayavi_scene )
            # Colormap
            # TODO: Figure out how to scale colorbar to smaller size
            #c = mlab.colorbar(orientation = "horizontal", 
                              #title = self.selected_measurement,
                              #figure = self.mayavi_view.trajectory_scene.mayavi_scene)
            #c.scalar_bar_representation.position2[1] = 0.05
            #c.scalar_bar.height = 0.05
            
            t2 = time.clock()
            logging.info("Computed trajectory layout (%.2f sec)"%(t2-t1))              
        else:
            logging.info("Re-drawing trajectories...")
            
            if self.do_plots_need_updating["trajectories"]:
                self.trajectory_line_collection.mlab_source.dataset.lines = self.trajectory_line_source.mlab_source.dataset.lines = np.array(self.directed_graph.edges())
                self.trajectory_line_collection.mlab_source.update()
                self.trajectory_line_source.mlab_source.update()                
                
                for key in self.connected_nodes.keys():
                    self.trajectory_label_collection[key].actor.actor.visibility = self.trajectory_selection[key]  

            if self.do_plots_need_updating["measurement"]:
                self.trajectory_line_collection.mlab_source.set(scalars = self.scalar_data)
                self.trajectory_node_collection.mlab_source.set(scalars = self.scalar_data)
            
            if self.do_plots_need_updating["colormap"]:
                self.trajectory_line_collection.module_manager.scalar_lut_manager.lut_mode = self.selected_colormap
                self.trajectory_node_collection.module_manager.scalar_lut_manager.lut_mode = self.selected_colormap
                
        # Re-enable the rendering
        self.mayavi_view.trajectory_scene.disable_render = False  

################################################################################
if __name__ == "__main__":
        
    import sys
    app = wx.PySimpleApp()
    logging.basicConfig(level=logging.DEBUG,)
    
    props = Properties.getInstance()

    # Load a properties file if passed in args
    if len(sys.argv) > 1:
        propsFile = sys.argv[1]
        props.LoadFile(propsFile)
        props = add_props_field(props)
    else:
        if not props.show_load_dialog():
            print 'Time Visualizer requires a properties file.  Exiting.'
            # Necessary in case other modal dialogs are up
            wx.GetApp().Exit()
            sys.exit()
        else:
            props = add_props_field(props)
            
    timelapsevisual = TimeLapseTool(None)
    timelapsevisual.Show()

    app.MainLoop()
    
    #
    # Kill the Java VM
    #
    try:
        from bioformats import jutil
        jutil.kill_vm()
    except:
        import traceback
        traceback.print_exc()
        print "Caught exception while killing VM"
