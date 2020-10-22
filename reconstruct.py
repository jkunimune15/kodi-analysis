import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm
from matplotlib.colors import ListedColormap, LinearSegmentedColormap
import pandas as pd
import os
import scipy.optimize as optimize
import scipy.signal as pysignal
import scipy.stats as stats
import scipy.special as special
from scipy.spatial import Delaunay
import gc
import re
import time

import diameter
import segnal as mysignal
from electric_field_model import get_analytic_brightness
from cmap import REDS, GREENS, BLUES, VIOLETS, GREYS, COFFEE

np.seterr('ignore')

SKIP_RECONSTRUCTION = False
SHOW_PLOTS = True
SHOW_RAW_PLOTS = False
SHOW_DEBUG_PLOTS = True
SHOW_OFFSET = False
VERBOSE = True
OBJECT_SIZE = 200e-4 # cm
THRESHOLD = 1e-4
ASK_FOR_HELP = False

VIEW_RADIUS = 5.0 # cm
NON_STATISTICAL_NOISE = 0.0
SPREAD = 1.05
EXPECTED_MAGNIFICATION_ACCURACY = 4e-3
RESOLUTION = 50

FOLDER = 'scans/'
SHOT = 'Shot number'
TIM = 'TIM'
APERTURE_RADIUS = 'Aperture Radius'
APERTURE_SPACING = 'Aperture Separation'
APERTURE_DISTANCE = 'L1'
MAGNIFICATION = 'Magnification'
ROTATION = 'Rotation'
ETCH_TIME = 'Etch time'
R_OFFSET = 'Offset (um)'
Θ_OFFSET = 'Offset theta (deg)'
Φ_OFFSET = 'Offset phi (deg)'
R_FLOW = 'Flow (km/s)'
Θ_FLOW = 'Flow theta (deg)'
Φ_FLOW = 'Flow phi (deg)'

TIM_LOCATIONS = [
	[np.nan,np.nan],
	[ 37.38, 162.00],
	[np.nan,np.nan],
	[ 63.44, 342.00],
	[100.81, 270.00],
	[np.nan,np.nan]]


def where_is_the_ocean(x, y, z, title, timeout=None):
	""" solicit the user's help in locating something """
	fig = plt.figure()
	plt.pcolormesh(x, y, z, vmax=np.quantile(z, .999))
	plt.axis('square')
	plt.colorbar()
	plt.title(title)

	center_guess = (None, None)
	def onclick(event):
		center_guess = (event.xdata, event.ydata)
	fig.canvas.mpl_connect('button_press_event', onclick)

	start = time.time()
	while center_guess[0] is None and (timeout is None or time.time() - start < timeout):
		plt.pause(.01)
	plt.close()
	if center_guess[0] is not None:
		return center_guess
	else:
		raise TimeoutError


def plot_raw_data(track_list, x_bins, y_bins, title):
	""" plot basic histograms of the tracks that have been loaded. """
	plt.figure()
	plt.hist2d(track_list['x(cm)'], track_list['y(cm)'], bins=(x_bins, y_bins))
	plt.xlabel("x (cm)", fontsize=16)
	plt.ylabel("y (cm)", fontsize=16)
	plt.title(title, fontsize=16)
	plt.gca().xaxis.set_tick_params(labelsize=16)
	plt.gca().yaxis.set_tick_params(labelsize=16)
	plt.axis('square')
	plt.tight_layout()

	plt.figure()
	plt.hist2d(track_list['d(µm)'], track_list['cn(%)'], bins=(np.linspace(0, 10, 51), np.linspace(0, 40, 41)), cmap=COFFEE, vmin=0, vmax=13000)
	plt.plot([2, 2], [0, 40], 'k--')
	plt.plot([3, 3], [0, 40], 'k--')
	plt.xlabel("Diameter (μm)", fontsize=16) # plot N(d,c)
	plt.ylabel("Contrast (%)", fontsize=16)
	plt.title(" ", fontsize=16)
	plt.gca().xaxis.set_tick_params(labelsize=16)
	plt.gca().yaxis.set_tick_params(labelsize=16)
	plt.tight_layout()
	plt.show()


def plot_cooked_data(track_x, track_y, xC_bins, yC_bins, xI_bins, yI_bins, N, x0, y0, r0, r_img):
	""" plot the data along with the initial fit to it, and the
		reconstructed superaperture.
	"""
	plt.figure()
	plt.hist2d(track_x, track_y, bins=(xC_bins, yC_bins))
	T = np.linspace(0, 2*np.pi)
	plt.plot(x0 + r0*np.cos(T), y0 + r0*np.sin(T), '--w')
	plt.plot(x0 - s0 + r0*np.cos(T), y0 + r0*np.sin(T), '--w')
	plt.plot(x0 + s0 + r0*np.cos(T), y0 + r0*np.sin(T), '--w')
	plt.plot(x0 + r_img*np.cos(T), y0 + r_img*np.sin(T), '--w')
	plt.axis('square')
	plt.colorbar()
	plt.show()
	plt.figure()
	plt.pcolormesh(xI_bins, yI_bins, N.T, vmax=np.quantile(N, .999))
	plt.axis('square')
	plt.colorbar()
	plt.show()


def project(r, θ, ɸ, basis):
	""" project given spherical coordinates (with angles in degrees) into the
		detector plane x and y, as well as z out of the page.
	"""
	θ, ɸ = np.radians(θ), np.radians(ɸ)
	v = [r*np.sin(θ)*np.cos(ɸ), r*np.sin(θ)*np.sin(ɸ), r*np.cos(θ)]
	return np.matmul(basis.T, v)


def simple_penumbra(r, δ, Q, r0, r_max, minimum, maximum, e_min=0, e_max=1):
	""" compute the shape of a simple analytic single-apeture penumbral image """
	rB, nB = get_analytic_brightness(r0, Q, e_min=e_min, e_max=e_max) # start by accounting for aperture charging but not source size
	if 4*δ >= r_max: # if the source size is over 1/4 of the image radius
		raise ValueError("δ is too big compared to r_max: 4*{}/{} >= 1".format(δ, r_max)) # give up
	elif 4*δ >= r_max/n_bins: # if 4*source size is smaller than the image radius but bigger than the pixel size
		r_kernel = np.linspace(-4*δ, 4*δ, int(4*δ/r_max*n_bins)*2+1) # make a little kernel
		n_kernel = np.exp(-r_kernel**2/δ**2)
		r_point = np.arange(-4*δ, r_max + 4*δ, r_kernel[1] - r_kernel[0]) # rebin the existing image to match the kernel spacing
		n_point = np.interp(r_point, rB, nB, right=0)
		assert len(n_point) >= len(n_kernel)
		penumbra = np.convolve(n_point, n_kernel, mode='same') # and convolve
	elif δ >= 0: # if 4*source size is smaller than one pixel and nonnegative
		r_point = np.linspace(0, r_max, n_bins) # use a dirac kernel instead of a gaussian
		penumbra = np.interp(r_point, rB, nB, right=0)
	else:
		raise ValueError("δ cannot be negative")
	w = np.interp(r, r_point, penumbra/np.max(penumbra), right=0) # map to the requested r values
	return minimum + (maximum-minimum)*w


def simple_fit(*args, a=1, b=0, c=1):
	""" compute how close these data are to this penumbral image """
	if len(args[0]) == 3 and len(args) == 12: # first, parse the parameters
		(x0, y0, δ), Q, r0, s0, r_img, minimum, maximum, X, Y, exp, e_min, e_max = args
	elif len(args[0]) == 4 and len(args) == 10:
		(x0, y0, δ, r0), s0, r_img, minimum, maximum, X, Y, exp, e_min, e_max = args
		Q = 0
	elif len(args[0]) == 4 and len(args) == 11:
		(x0, y0, δ, Q), r0, s0, r_img, minimum, maximum, X, Y, exp, e_min, e_max = args
	elif len(args[0]) == 7 and len(args) == 11:
		(x0, y0, δ, Q, a, b, c), r0, s0, r_img, minimum, maximum, X, Y, exp, e_min, e_max = args
	else:
		raise ValueError("unsupported set of arguments")
	if Q < 0 or abs(x0) > VIEW_RADIUS or abs(y0) > VIEW_RADIUS: return float('inf') # and reject impossible ones

	x_eff = a*(X - x0) + b*(Y - y0)
	y_eff = b*(X - x0) + c*(Y - y0)
	teo = np.zeros(X.shape) # build up the theoretical image
	include = np.full(X.shape, False) # and decide at which pixels to even look
	for i in range(-6, 6):
		dy = i*np.sqrt(3)/2*s0
		for j in range(-6, 6):
			dx = (2*j + i%2)*s0/2
			if np.hypot(dx, dy) < VIEW_RADIUS - r_img:
				r_rel = np.hypot(x_eff - dx, y_eff - dy)
				try:
					teo[r_rel <= r_img] += simple_penumbra(r_rel[r_rel <= r_img], δ, Q, r0, r_img, 0, 1, e_min, e_max) # as an array of penumbrums
				except ValueError:
					return np.inf
				include[r_rel <= r_img] = True
				if np.any(np.isnan(teo)):
					return np.inf
	sigma2 = 1 + teo + (NON_STATISTICAL_NOISE*teo)**2
	
	if np.sum(include) == 0:
		return np.inf
	if minimum is None: # if the max and min are unspecified
		scale, minimum = mysignal.linregress(teo, exp, include/sigma2)
		maximum = minimum + scale
	if minimum > maximum:
		return np.inf
	teo = minimum + teo*(maximum - minimum)
	error = np.sum((exp - teo)**2/sigma2, where=include) - 2*np.sum(include) # use a gaussian error model
	penalty = \
		- 2*np.sum(include) \
		+ (a**2 + 2*b**2 + c**2)/(4*EXPECTED_MAGNIFICATION_ACCURACY**2) \
		+ Q/.05 - X.size*np.log(maximum/minimum - 1)
	return error + penalty


if __name__ == '__main__':
	shot_list = pd.read_csv('shot_list.csv')

	for i, scan in shot_list.iterrows():
		Q = None
		L = scan[APERTURE_DISTANCE] # cm
		M = scan[MAGNIFICATION] # cm
		rA = scan[APERTURE_RADIUS]/1.e4 # cm
		sA = scan[APERTURE_SPACING]/1.e4 # cm
		rotation = np.radians(scan[ROTATION]) # rad
		if sA == 0: sA = 6*VIEW_RADIUS/(M + 1)
		etch_time = float(scan[ETCH_TIME].strip(' h'))

		θ_TIM, ɸ_TIM = np.radians(TIM_LOCATIONS[int(scan[TIM])-1])
		basis = np.array([
			[0, 0, 0],
			[np.sin(θ_TIM-np.pi/2)*np.cos(ɸ_TIM), np.sin(θ_TIM-np.pi/2)*np.sin(ɸ_TIM), np.cos(θ_TIM-np.pi/2)],
			[np.sin(θ_TIM)*np.cos(ɸ_TIM), np.sin(θ_TIM)*np.sin(ɸ_TIM), np.cos(θ_TIM)],
		]).T
		basis[:,0] = np.cross(basis[:,1], basis[:,2])

		x_off, y_off, z_off = project(float(scan[R_OFFSET]), float(scan[Θ_OFFSET]), float(scan[Φ_OFFSET]), basis)*1e-4 # cm
		x_flo, y_flo, z_flo = project(float(scan[R_FLOW]), float(scan[Θ_FLOW]), float(scan[Φ_FLOW]), basis)*1e-4 # cm/ns

		filename = None
		for fname in os.listdir(FOLDER):
			if fname.endswith('.txt') and str(scan[SHOT]) in fname and 'tim'+str(scan[TIM]) in fname.lower() and scan[ETCH_TIME].replace(' ','') in fname:
				filename = fname
				print("Beginning reconstruction for TIM {} on shot {}".format(scan[TIM], scan[SHOT]))
				break
		if filename is None:
			print("Could not find text file for TIM {} on shot {}".format(scan[TIM], scan[SHOT]))
			continue
		track_list = pd.read_csv(FOLDER+filename, sep=r'\s+', header=20, skiprows=[24], encoding='Latin-1', dtype='float32')

		r0 = (M + 1)*rA
		s0 = (M + 1)*sA
		r_img = SPREAD*r0 + M*OBJECT_SIZE
		VIEW_RADIUS = max(np.max(track_list['x(cm)']), np.max(track_list['y(cm)']))
		n_bins = min(1000, int(RESOLUTION/(OBJECT_SIZE*M)*r_img)) # get the image resolution needed to resolve the object

		x_temp, y_temp = track_list['x(cm)'].copy(), track_list['y(cm)'].copy()
		track_list['x(cm)'] =  np.cos(rotation+np.pi)*x_temp - np.sin(rotation+np.pi)*y_temp # apply any requested rotation, plus 180 flip to deal with inherent flip due to aperture
		track_list['y(cm)'] =  np.sin(rotation+np.pi)*x_temp + np.cos(rotation+np.pi)*y_temp
		if re.fullmatch(r'[0-9]+', str(scan[SHOT])): # adjustments for real data:
			track_list['ca(%)'] -= np.min(track_list['cn(%)']) # shift the contrasts down if they're weird
			track_list['cn(%)'] -= np.min(track_list['cn(%)'])
			track_list['d(µm)'] -= np.min(track_list['d(µm)']) # shift the diameters over if they're weird
		hicontrast = (track_list['cn(%)'] < 35) & (track_list['e(%)'] < 15)

		track_list['x(cm)'] -= np.mean(track_list['x(cm)'][hicontrast]) # do your best to center
		track_list['y(cm)'] -= np.mean(track_list['y(cm)'][hicontrast])

		xC_bins, yC_bins = np.linspace(-VIEW_RADIUS, VIEW_RADIUS, n_bins+1), np.linspace(-VIEW_RADIUS, VIEW_RADIUS, n_bins+1) # this is the CR39 coordinate system, centered at 0,0
		dxC, dyC = xC_bins[1] - xC_bins[0], yC_bins[1] - yC_bins[0] # get the bin widths
		xC, yC = (xC_bins[:-1] + xC_bins[1:])/2, (yC_bins[:-1] + yC_bins[1:])/2 # change these to bin centers
		XC, YC = np.meshgrid(xC, yC, indexing='ij') # change these to matrices

		if SHOW_RAW_PLOTS:
			plot_raw_data(track_list[hicontrast], xC_bins, yC_bins, f"Penumbral image, TIM{scan[TIM]}, shot {scan[SHOT]}")
		if SKIP_RECONSTRUCTION:
			continue

		image_layers, x_layers, y_layers = [], [], []

		if np.std(track_list['d(µm)']) == 0:
			cuts = [('plasma', [0, 5])]
		else:
			cuts = [(GREYS, [0, 13]), (REDS, [0, 5]), (GREENS, [5, 9]), (BLUES, [9, 13])] # [MeV] (post-filtering)

		for color, (cmap, e_out_bounds) in enumerate(cuts):
			d_bounds = diameter.D(np.array(e_out_bounds), τ=etch_time)[::-1] # make some diameter cuts
			e_in_bounds = np.clip(np.array(e_out_bounds) + 2, 0, 12)
			track_x = track_list['x(cm)'][hicontrast & (track_list['d(µm)'] >= d_bounds[0]) & (track_list['d(µm)'] < d_bounds[1])].to_numpy()
			track_y = track_list['y(cm)'][hicontrast & (track_list['d(µm)'] >= d_bounds[0]) & (track_list['d(µm)'] < d_bounds[1])].to_numpy()
			if len(track_x) <= 0:
				print("No tracks found in this cut.")
				continue
			print(d_bounds)

			# Q = None

			N, xC_bins, yC_bins = np.histogram2d( # make a histogram
				track_x, track_y, bins=(xC_bins, yC_bins))
			assert N.shape == XC.shape

			if ASK_FOR_HELP:
				try: # ask the user for help finding the center
					x0, y0 = where_is_the_ocean(xC_bins, yC_bins, N, "Please click on the center of a penumbrum.", timeout=8.64)
				except:
					x0, y0 = (0, 0)
			else:
				x0, y0 = (0, 0)

			if Q is None:
				opt = optimize.minimize(simple_fit, x0=[None]*4, args=(r0, s0, r_img, None, None, XC, YC, N, *e_in_bounds),
					method='Nelder-Mead', options=dict(initial_simplex=[
						[x0+r_img/2, y0,         OBJECT_SIZE*M/3, 1.0e-1],
						[x0-r_img/2, y0+r_img/2, OBJECT_SIZE*M/3, 1.0e-1],
						[x0-r_img/2, y0-r_img/2, OBJECT_SIZE*M/3, 1.0e-1],
						[x0,         y0,         OBJECT_SIZE*M/2, 1.0e-1],
						[x0,         y0,         OBJECT_SIZE*M/3, 1.9e-1]]))
				x0, y0, δ, Q = opt.x
			else:
				opt = optimize.minimize(simple_fit, x0=[None]*3, args=(Q, r0, s0, r_img, None, None, XC, YC, N, *e_in_bounds),
					method='Nelder-Mead', options=dict(initial_simplex=[
						[x0+r_img/2, y0, OBJECT_SIZE*M/3],
						[x0-r_img/2, y0+r_img/2, OBJECT_SIZE*M/3],
						[x0-r_img/2, y0-r_img/2, OBJECT_SIZE*M/3],
						[x0, y0, OBJECT_SIZE*M/2]]))
				x0, y0, δ = opt.x
			if VERBOSE: print(opt)
			print("n = {0:.4g}, (x0, y0) = ({1:.3f}, {2:.3f}), δ = {3:.3f} μm, Q = {4:.3f} cm/MeV, M = {5:.2f}".format(np.sum(N), x0, y0, δ/M/1e-4, Q, M))

			xI_bins, yI_bins = np.linspace(x0 - r_img, x0 + r_img, n_bins+1), np.linspace(y0 - r_img, y0 + r_img, n_bins+1) # this is the CR39 coordinate system, but encompassing a single superpenumbrum
			dxI, dyI = xI_bins[1] - xI_bins[0], yI_bins[1] - yI_bins[0]
			xI, yI = (xI_bins[:-1] + xI_bins[1:])/2, (yI_bins[:-1] + yI_bins[1:])/2
			XI, YI = np.meshgrid(xI, yI, indexing='ij')
			N = np.zeros(XI.shape) # and N combines all penumbra on that square
			for i in range(-6, 6):
				dy = i*np.sqrt(3)/2*s0
				for j in range(-6, 6):
					dx = (2*j + i%2)*s0/2
					if np.hypot(dx, dy) + r_img <= VIEW_RADIUS:
						N += np.histogram2d(track_x, track_y, bins=(xI_bins + dx, yI_bins + dy))[0] # do that histogram

			kernel_size = int(2*SPREAD*r0/dxI) + 1
			if kernel_size%2 == 0:
				kernel_size += 1
			xK_bins, yK_bins = np.linspace(-dxI*kernel_size/2, dxI*kernel_size/2, kernel_size+1), np.linspace(-dyI*kernel_size/2, dyI*kernel_size/2, kernel_size+1)
			dxK, dyK = xK_bins[1] - xK_bins[0], yK_bins[1] - yK_bins[0]
			XK, YK = np.meshgrid((xK_bins[:-1] + xK_bins[1:])/2, (yK_bins[:-1] + yK_bins[1:])/2, indexing='ij') # this is the kernel coordinate system, measured from the center of the umbra

			xS_bins, yS_bins = xI_bins[kernel_size//2:-(kernel_size//2)]/M, yI_bins[kernel_size//2:-(kernel_size//2)]/M # this is the source system.
			dxS, dyS = xS_bins[1] - xS_bins[0], yS_bins[1] - yS_bins[0]
			xS, yS = (xS_bins[:-1] + xS_bins[1:])/2, (yS_bins[:-1] + yS_bins[1:])/2 # change these to bin centers
			XS, YS = np.meshgrid(xS, yS, indexing='ij')

			if SHOW_PLOTS:
				plot_cooked_data(track_x, track_y, xC_bins, yC_bins, xI_bins, yI_bins, N, x0, y0, r0, r_img)

			del(track_x)
			del(track_y)
			gc.collect()

			penumbral_kernel = np.zeros(XK.shape) # build the penumbral kernel
			for dx in [-dxK/3, 0, dxK/3]: # sampling over a few pixels
				for dy in [-dyK/3, 0, dyK/3]:
					penumbral_kernel += simple_penumbra(np.hypot(XK+dxK, YK+dyK), 0, Q, r0, r_img, 0, 1, *e_in_bounds)
			penumbral_kernel = penumbral_kernel/np.sum(penumbral_kernel)

			background = np.average(N,
				weights=(np.hypot(XI - x0, YI - y0) > (r_img + r0)/2)) # compute these with the better centering
			umbra = np.average(N,
				weights=(np.hypot(XI - x0, YI - y0) < r0/2))
			D = simple_penumbra(np.hypot(XI - x0, YI - y0), δ, Q, r0, r_img, background, umbra, *e_in_bounds) # make D equal to the rough fit to N

			penumbra_low = np.quantile(penumbral_kernel/penumbral_kernel.max(), .05)
			penumbra_hih = np.quantile(penumbral_kernel/penumbral_kernel.max(), .70)
			reach = pysignal.convolve2d(np.ones(XS.shape), penumbral_kernel, mode='full')
			data_bins = np.isfinite(N) & (reach/reach.max() > penumbra_low) & (reach/reach.max() < penumbra_hih) # exclude bins that are NaN and bins that are touched by all or none of the source pixels
			data_bins &= ~((N == 0) & (Delaunay(np.transpose([XI[N > 0], YI[N > 0]])).find_simplex(np.transpose([XI.ravel(), YI.ravel()])) == -1).reshape(N.shape)) # crop it at the convex hull where counts go to zero

			B, χ2 = mysignal.gelfgat_deconvolve2d(
				N - background,
				penumbral_kernel,
				D,
				THRESHOLD,
				where=data_bins,
				illegal=np.hypot(XS - (xS[0] + xS[-1])/2, YS - (yS[0] + yS[-1])/2) >= (xS[-1] - xS[0])/2 + (xS[1] - xS[0]),
				verbose=VERBOSE,
				show_plots=SHOW_DEBUG_PLOTS) # deconvolve!
			if χ2/np.sum(data_bins) >= 2.0: # throw it away if it looks unreasonable
				print("Could not find adequate fit.")
				continue
			B = np.maximum(0, B) # we know this must be positive

			plt.figure()
			plt.pcolormesh(xS_bins/1e-4, yS_bins/1e-4, B.T, cmap=cmap, vmin=0)
			plt.colorbar()
			plt.axis('square')
			plt.title("B(x, y) of TIM {} on shot {} with d ∈ [{:.1f}μm,{:.1f}μm)".format(scan[TIM], scan[SHOT], *d_bounds))
			plt.xlabel("x (μm)")
			plt.ylabel("y (μm)")
			plt.axis([(x0/M - OBJECT_SIZE)/1e-4, (x0/M + OBJECT_SIZE)/1e-4, (y0/M - OBJECT_SIZE)/1e-4, (y0/M + OBJECT_SIZE)/1e-4])
			plt.tight_layout()
			plt.savefig("results/{} TIM{} {:.1f}-{:.1f} {}h.png".format(scan[SHOT], scan[TIM], *d_bounds, etch_time))

			if SHOW_PLOTS:
				plt.show()

			image_layers.append(B/B.max())
			x_layers.append(XS)
			y_layers.append(YS)

		try:
			xray = np.loadtxt('scans/KoDI_xray_data1 - {:d}-TIM{:d}-{:d}.mat.csv'.format(int(scan[SHOT]), int(scan[TIM]), [2,4,5].index(int(scan[TIM]))+1), delimiter=',')
		except (ValueError, OSError):
			xray = None
		if xray is not None:
			plt.figure()
			# plt.pcolormesh(np.linspace(-300, 300, 3), np.linspace(-300, 300, 3), np.zeros((2, 2)), cmap=VIOLETS, vmin=0, vmax=1)
			plt.pcolormesh(np.linspace(-100, 100, 101), np.linspace(-100, 100, 101), xray, cmap=VIOLETS, vmin=0)
			plt.colorbar()
			plt.axis('square')
			plt.title("X-ray image of TIM {} on shot {}".format(scan[TIM], scan[SHOT]))
			plt.xlabel("x (μm)")
			plt.ylabel("y (μm)")
			plt.axis([-100, 100, -100, 100])
			plt.tight_layout()
			plt.savefig("results/{} TIM{} xray sourceimage.png".format(scan[SHOT], scan[TIM]))
			plt.close()

		if len(image_layers) > 1:
			x0 = x_layers[0][np.unravel_index(np.argmax(image_layers[0]), image_layers[0].shape)]
			y0 = y_layers[0][np.unravel_index(np.argmax(image_layers[0]), image_layers[0].shape)]

			plt.figure()
			plt.contourf((x_layers[1] - x0)/1e-4, (y_layers[1] - y0)/1e-4, image_layers[1], levels=[0, 0.25, 1], colors=['#00000000', '#FF5555BB', '#000000FF'])
			# plt.contourf((x_layers[2] - x0)/1e-4, (y_layers[2] - y0)/1e-4, image_layers[2], levels=[0, 0.25, 1], colors=['#00000000', '#55FF55BB', '#000000FF'])
			plt.contourf((x_layers[3] - x0)/1e-4, (y_layers[3] - y0)/1e-4, image_layers[3], levels=[0, 0.25, 1], colors=['#00000000', '#5555FFBB', '#000000FF'])
			if xray is not None:
				plt.contour(np.linspace(-100, 100, 100), np.linspace(-100, 100, 100), xray, levels=[.25], colors=['#550055BB'])
			if SHOW_OFFSET:
				plt.plot([0, x_off/1e-4], [0, y_off/1e-4], '-k')
				plt.scatter([x_off/1e-4], [y_off/1e-4], color='k')
				plt.arrow(0, 0, x_flo/1e-4, y_flo/1e-4, color='k', head_width=5, head_length=5, length_includes_head=True)
				plt.text(0.05, 0.95, "offset out of page = {:.3f}\nflow out of page = {:.3f}".format(z_off/r_off, z_flo/r_flo),
					verticalalignment='top', transform=plt.gca().transAxes, fontsize=12)
			plt.axis('square')
			plt.axis([-150, 150, -150, 150])
			plt.xlabel("x (μm)")
			plt.ylabel("y (μm)")
			plt.title("TIM {} on shot {}".format(scan[TIM], scan[SHOT]))
			plt.tight_layout()
			plt.savefig("results/{} TIM{} nestplot.png".format(scan[SHOT], scan[TIM]))
			plt.close()

		if len(image_layers) > 0:
			p0, (p1, θ1), (p2, θ2) = mysignal.shape_parameters(XS, YS, image_layers[0])
			print(f"P0 = {p0/1e-4:.2f}μm")
			print(f"P1 = {p1/1e-4:.2f}μm = {p1/p0*100:.1f}%, θ = {np.degrees(θ1)}°")
			print(f"P2 = {p2/1e-4:.2f}μm = {p2/p0*100:.1f}%, θ = {np.degrees(θ2)}°")
