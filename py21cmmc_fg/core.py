#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Tue Apr 17 14:13:39 2018

@author: bella

Foreground core for 21cmmc

"""
from scipy.integrate import quad
import numpy as np
from astropy import constants as const
from astropy.cosmology import Planck15 as cosmo  # TODO: this is not quite correct, if we pass cosmo parameters.
from astropy import units as un
from scipy.interpolate import RegularGridInterpolator
from powerbox.dft import fft
from powerbox import LogNormalPowerBox
from os import path
from py21cmmc import LightCone


class CoreForegrounds:
    def __init__(self, pt_source_params={}, diffuse_params = {},  add_point_sources=True, add_diffuse=True, redshifts=None,
                 boxsize=None, sky_cells = None):
        """
        Setting up variables minimum and maximum flux
        """
        # print "Initializing the foreground core"

        self.pt_source_params = pt_source_params
        self.diffuse_params = diffuse_params
        self.add_diffuse = add_diffuse
        self.add_point_sources = add_point_sources

        self.redshifts = redshifts

        if np.any(np.diff(self.redshifts)< 0) :
            print("yeah in here")
            self.redshifts = self.redshifts[::-1]

        if np.any(np.diff(self.redshifts) < 0) :
            raise ValueError("Redshifts need to be monotonically increasing. %s"%self.redshifts)

        self.boxsize = boxsize
        self.sky_cells = sky_cells

    def setup(self):
        print("Generating the foregrounds")

    def __call__(self, ctx):
        """
        Apply foregrounds to an existing EoR lightcone.

        The net result of this function is to replace the EoR lightcone in the "output" variable of the context with
        a foreground-contaminated version with the same shape (but in Jy/sr rather than mK). It also adds the
        variables "frequencies" and "sky_size".
        """
        print("Getting the simulation data")

        eor = ctx.get("output", None)
        if eor is not None:
            eor_lightcone = ctx.get("output").lightcone_box
            redshifts = ctx.get("output").redshifts_slices
            boxsize = ctx.get("output").box_len
            sky_cells = eor_lightcone.shape[0]
        else:
            redshifts = self.redshifts
            boxsize = self.boxsize
            sky_cells = self.sky_cells

        fg_lightcone, frequencies, sky_size = self.add_foregrounds(sky_cells, redshifts, boxsize)

        if eor is None:
            ctx.add("output", LightCone(redshifts, fg_lightcone, fg_lightcone.shape[0], boxsize))
            print("Added lightcone")
            ctx.get('output').redshifts_slices = redshifts
        else:
            # Replace the EoR lightcone with the new lightcone
            # Make sure we convert from mK to Jy before summing!
            eor_lightcone *= self.conversion_factor_K_to_Jy()
            ctx.get("output").lightcone_box += fg_lightcone

        ctx.add("frequencies", frequencies)
        ctx.add("sky_size", sky_size)

    def add_foregrounds(self, sky_cells, redshifts, boxsize):
        """
        A function which creates foregrounds (both point-sources and diffuse) and adds them to an existing lightcone.
        It also changes the units from mK to Jy/sr.

        Parameters
        ----------
        sky_cells : int
            The number of cells on the sky grid (not into the sky).
        
        redshifts : (nredshifts,)-array
            The redshifts (instead of comoving distance) corresponding to each slice of EoR lightcone
        
        boxsize : float
            The size of the EoR lightcone (in transverse direction) in Mpc

        Returns
        -------
        sky : (sky_cells, sky_cells, nredshifts)-array
            A box containing both the EoR signal and the foregrounds, in units of Jy/sr
        
        frequencies : (nredshifts,)-array
            The frequencies (in Hz) corresponding to the input redshifts.

        sky_size : float
            The length of the box (in transverse direction) in radians, at the mean redshift.
        
        """
        # Number of 2D cells in sky array
        # sky_cells = lightcone.shape[0]

        # Convert the boxsize from Mpc to radian
        # IF USING SMALL BOX, THIS IS WHERE WE DO STITCHING!!!
        sky_size = self.get_sky_size(boxsize, redshifts)

        # Note, don't flip the frequencies here, rather do it only when necessary.
        frequencies = 1420e6 / (redshifts + 1)

        lightcone = np.zeros((sky_cells, sky_cells, len(redshifts)))

        if self.add_diffuse:
            # We add it here, because it's easier to add in mK
            lightcone += self.diffuse(frequencies, sky_cells, sky_size, **self.diffuse_params)

        # Change the units of brightness temperature from mK to Jy/sr
        lightcone *= self.conversion_factor_K_to_Jy()


        # Interpolate linearly in frequency (POSSIBLY IN RADIAN AS WELL)
        # TODO: I don't think we need to do this, as we can interpolate in the Instrumenal class.
        # linLightcone, linFrequencies = self.interpolate_freqs(EoR_lightcone, frequencies)

        # Generate the point sources foregrounds and
        # TODO: this has no dependence on spectral index!!!
        if self.add_point_sources:
            lightcone += np.repeat(
                self.point_sources(
                    frequencies=frequencies, sky_cells = sky_cells, sky_size=sky_size, **self.pt_source_params
                ),
                np.shape(lightcone)[2], axis=2
            )

        return lightcone, frequencies, sky_size

    @staticmethod
    def get_sky_size(boxsize, redshifts):
        return 2 * np.arctan(boxsize / (2 * cosmo.comoving_transverse_distance([np.mean(redshifts)]).value))


    # def interpolate_freqs(self, data, frequencies, uv_range=100):
    #     """
    #     Interpolate the irregular frequencies so that they are linearly-spaced
    #     """
    #
    #     linFrequencies = np.linspace(np.min(frequencies), np.max(frequencies), data.shape[2])
    #
    #     ncells = np.shape(data)[0]
    #     # Create the xy data
    #     xy = np.linspace(-uv_range, uv_range, ncells)
    #
    #     # generate the interpolation function
    #     func = RegularGridInterpolator([xy, xy, frequencies], data, bounds_error=False, fill_value=0)
    #
    #     # Create a meshgrid to interpolate the points
    #     XY, YX, LINZREDS = np.meshgrid(xy, xy, linFrequencies)
    #
    #     # Flatten the arrays so the can be put into pts array
    #     XY = XY.flatten()
    #     YX = YX.flatten()
    #     LINZREDS = LINZREDS.flatten()
    #
    #     # Create the points to interpolate
    #     numpts = XY.size
    #     pts = np.zeros([numpts, 3])
    #     pts[:, 0], pts[:, 1], pts[:, 2] = XY, YX, LINZREDS
    #
    #     # Interpolate the points
    #     interpData = func(pts)
    #
    #     # Reshape the data
    #     interpData = interpData.reshape(ncells, ncells, len(linFrequencies))
    #
    #     return interpData, linFrequencies

    @staticmethod
    def point_sources(frequencies, sky_cells, sky_size, S_min=1e-1, S_max=1.0, alpha=4100., beta=1.59):
        """
        Create a grid of flux densities corresponding to a sample of point-sources drawn from a power-law source count
        model.

        Parameters
        ----------
        frequencies : array
            The frequencies at which to determine the diffuse emission.

        sky_cells : int
            Number of cells on a side for the 2 sky dimensions.

        sky_size : float
            Size (in radians) of the angular dimensions of the box.

        S_min
        S_max
        alpha
        beta

        Returns
        -------

        """
        # Create a function for source count distribution
        source_count = lambda x: alpha * x ** (-beta)

        # Find the mean number of sources
        n_bar = quad(source_count, S_min, S_max)[0]

        # Generate the number of sources following poisson distribution
        N_sources = np.random.poisson(n_bar)

        # Generate the point sources in unit of Jy and position using uniform distribution
        fluxes = ((S_max ** (1 - beta) - S_min ** (1 - beta)) * np.random.uniform(size=N_sources) + S_min ** (
                    1 - beta)) ** (1 / (1 - beta))
        pos = np.rint(np.random.uniform(0, sky_cells - 1, size=(N_sources, 2))).astype(int)

        # Create an empty array and fill it up by adding the point sources
        sky = np.zeros((sky_cells, sky_cells, 1))

        for ii in range(N_sources):
            sky[pos[ii, 0], pos[ii, 1]] += fluxes[ii]

        # Divide by area of each sky cell; Jy/sr
        sky /= (sky_size / sky_cells)**2

        return sky

    @staticmethod
    def diffuse(frequencies, ncells, sky_size,
                u0=10.0,
                eta = 0.01,
                rho = -2.7,
                mean_temp=253e3,
                kappa=-2.55):
        """
        This creates diffuse structure according to Eq. 55 from Trott+2016.

        Parameters
        ----------
        frequencies : array
            The frequencies at which to determine the diffuse emission.

        ncells : int
            Number of cells on a side for the 2 sky dimensions.

        sky_size : float
            Size (in radians) of the angular dimensions of the box.

        u0, eta, rho, mean_temp, kappa : float, optional
            Parameters of the diffuse sky model (see notes)

        Returns
        -------
        Tbins : (ncells, ncells, nfreq)- array
            Brightness temperature of the diffuse emission, in mK.


        Notes
        -----
        The box is populated with a lognormal field with power spectrum given by

        .. math:: \left(\frac{2k_B}{\lambda^2}\right)^2 \Omega (eta \bar{T}_B) \left(\frac{u}{u_0}\right)^{\rho} \left(\frac{\nu}{100 {\rm MHz}}\right)^{\kappa}
        """

        # Calculate mean flux density per frequency. Remember to take the square root because the Power is squared.
        Tbar = np.sqrt((frequencies/1e8)**kappa * mean_temp**2)
        power_spectrum = lambda  u : eta**2 * (u/u0) ** rho

        # Create a log normal distribution of fluctuations
        pb = LogNormalPowerBox(N=ncells, pk=power_spectrum, dim=2, boxlength=sky_size[0], a=0, b=2 * np.pi, seed=1234)

        density = pb.delta_x() + 1

        # Multiply the inherent fluctuations by the mean flux density.
        if np.std(density)>0:
            Tbins = np.outer(density, Tbar).reshape((ncells, ncells, len(frequencies)))
        else:
            Tbins = np.zeros((ncells, ncells, len(frequencies)))
            for i in range(len(frequencies)):
                Tbins[:, :, i] = Tbar[i]

        return Tbins

    @staticmethod
    def conversion_factor_K_to_Jy(nu=None):
        """

        Parameters
        ----------
        nu : float array, optional
            The frequencies of the observation

        Returns
        -------
        conversion_factor : float or array
            The conversion factor(s) (per frequency) which convert temperature in Kelvin to flux density in Jy.

        """
        # Can either do it with the beam or without the beam (frequency dependent)
        if nu is None:
            # TODO: unfortunately, this is instrument-dependent and so should really go in the instrumental core
            A_eff = 20 * un.m ** 2

            flux_density = (2 * 1e26 * const.k_B * 1e-3 * un.K / (A_eff * (1 * un.Hz) * (1 * un.s))).to(
                un.W / (un.Hz * un.m ** 2))

        else:
            flux_density = (2 * const.k_B * 1e-3 * un.K / (((const.c) / (nu.to(1 / un.s))) ** 2) * 1e26).to(
                un.W / (un.Hz * un.m ** 2))

        return flux_density.value


class CoreInstrumental:
    def __init__(self, antenna_posfile, freq_min, freq_max, nfreq, tile_diameter=4.0, max_bl_length=150.0,
                 integration_time=1200, Tsys = 0):
        """
        Core MCMC class which converts 21cmFAST *lightcone* output into a mock observation, sampled at specific baselines.

        Assumes that a :class:`ForegroundCore` is also being used (and loaded before this).

        Parameters
        ----------
        antenna_posfile : str, {"mwa_phase2", "ska_low_v5"}
            Path to a file containing antenna positions. File must be in the format such that the second column is the
            x position (in metres), and third column the y position (in metres). Some files are built-in, and these can
            be accessed by using the options defined above.

        freq_min, freq_max : float
            min/max frequencies of the observation, in MHz.

        nfreq : int
            Number of frequencies in the observation.

        tile_diameter : float, optional
            The physical diameter of the tiles, in metres.

        max_bl_length : float, optional
            The maximum length (in metres) of the baselines to be included in the analysis.

        integration_time : float,optional
            The length of the observation, in seconds.
        """
        self.antenna_posfile = antenna_posfile
        self.instrumental_frequencies = np.linspace(freq_min*1e6, freq_max*1e6, nfreq)
        self.tile_diameter = tile_diameter * un.m
        self.max_bl_length = max_bl_length
        self.integration_time = integration_time
        self.Tsys = Tsys

    def setup(self):
        """
        Basically just read in the baselines from file that user gives.
        """
        if self.antenna_posfile == "grid_centres":
            self.baselines = None

        else:
            # If antenna_posfile is a simple string, we'll try to find it in the data directory.
            data_path = path.join(path.dirname(__file__), 'data', self.antenna_posfile+'.txt')

            if path.exists(data_path):
                ant_pos = np.genfromtxt(data_path, float)
            else:
                ant_pos = np.genfromtxt(self.antenna_posfile, float)


            # Find all the possible combination of tile displacement
            # baselines is a dim2 array of x and y displacements.
            self.baselines = self.get_baselines(ant_pos[:, 1], ant_pos[:, 2]) * un.m

            self.baselines = self.baselines[
                self.baselines[:, 0].value ** 2 + self.baselines[:, 1].value ** 2 <= self.max_bl_length ** 2]

    def __call__(self, ctx):
        """
        Generate a set of realistic visibilities (i.e. the output we expect from an interferometer) and add it to the
        context. Also, add the linear frequencies of the observation to the context.
        """
        lightcone = ctx.get("output").lightcone_box
        boxsize = ctx.get("output").box_len
        redshifts = ctx.get("output").redshifts_slices

        # Try getting the frequencies and sky size, but if they don't exist, just calculate them.
        frequencies = ctx.get("frequencies", 1420e6/(1+redshifts))
        sky_size = ctx.get("sky_size", CoreForegrounds.get_sky_size(boxsize, redshifts))

        vis = self.add_instrument(lightcone, frequencies, sky_size)

        ctx.add("visibilities", vis)
        ctx.add("baselines", self.baselines)
        ctx.add("frequencies", self.instrumental_frequencies)

    def add_instrument(self, lightcone, frequencies, sky_size):
        # Number of 2D cells in sky array
        sky_cells = np.shape(lightcone)[0]

        # Add the beam attenuation
        beam_sky = lightcone * self.beam(frequencies, sky_cells, sky_size, self.tile_diameter)

        # Fourier transform image plane to UV plane.
        uvplane, uv = self.image_to_uv(beam_sky, sky_size)

        # This is probably bad, but set baselines if none are given, to coincide exactly with the uv grid.
        if self.baselines is None:
            self.baselines = np.zeros((len(uv[0])**2, 2))
            U,V = np.meshgrid(uv[0], uv[1])
            self.baselines[:, 0] = U.flatten()*(const.c/frequencies.max())
            self.baselines[:, 1] = V.flatten()*(const.c/frequencies.max())

        # Fourier Transform over the (u,v) dimension and baselines sampling
        visibilities = self.sample_onto_baselines(uvplane, uv, self.baselines, frequencies)
        visibilities = self.interpolate_frequencies(visibilities, frequencies, self.instrumental_frequencies)

        # Just in case we forget, now the frequencies are all in terms of the instrumental frequencies.
        frequencies = self.instrumental_frequencies

        # Add thermal noise
        visibilities = self.add_thermal_noise(visibilities, frequencies, self.integration_time, Tsys=self.Tsys)

        return visibilities

    @staticmethod
    def beam(frequencies, ncells, sky_size, D):
        """
        Generate a frequency-dependent Gaussian beam attenuation across the sky per frequency.

        Parameters
        ----------
        frequencies : array
            A set of frequencies (in Hz) at which to evaluate the beam.

        ncells : int
            Number of cells in the sky grid.

        sky_size : float
            The extent of the sky in radians.

        Returns
        -------
        beam : (ncells, ncells, nfrequencies)-array
            The beam attenuation (maximum unity) over the sky.
        """
        # First find the sigma of the beam
        epsilon = 0.42

        frequencies = frequencies / un.s
        sigma = ((epsilon * const.c) / (frequencies * D)).to(un.dimensionless_unscaled)

        # Then create a meshgrid for the beam attenuation on sky array
        sky_coords_lm = np.sin(np.linspace(-sky_size / 2, sky_size / 2, ncells))
        L, M = np.meshgrid(sky_coords_lm, sky_coords_lm)
        
        return np.exp(np.outer(-(L ** 2 + M ** 2), 1. / sigma ** 2).reshape((ncells, ncells, len(frequencies))))

    @staticmethod
    def image_to_uv(sky, L):
        """
        Transform a box from image plan to UV plane.

        Parameters
        ----------
        sky : (ncells, ncells, nfreq)-array
            The frequency-dependent sky brightness (in arbitrary units)

        L : float
            The size of the box in radians.

        Returns
        -------
        uvsky : (ncells, ncells, nfreq)-array
            The UV-plane representation of the sky. Units are units of the sky times radians.

        uv_scale : list of two arrays.
            The u and v co-ordinates of the uvsky, respectively. Units are inverse of L.
        """
        ft, uv_scale = fft(sky, [L, L], axes=(0, 1))
        return ft, uv_scale

    @staticmethod
    def sample_onto_baselines(uvplane, uv, baselines, frequencies):
        """
        Sample a gridded UV sky onto a set of baselines.

        Sampling is done via linear interpolation over the regular grid.

        Parameters
        ----------
        uvplane : (ncells, ncells, nfreq)-array
            The gridded UV sky, in Jy.

        uv : list of two 1D arrays
            The u and v coordinates of the uvplane respectively.

        baselines : (N,2)-array
            Each row should be the (x,y) co-ordinates of a baseline, in metres.

        frequencies : 1D array
            The frequencies of the uvplane.

        Returns
        -------
        vis : complex (N, nfreq)-array
             The visibilities defined at each baseline.

        """
        vis = np.zeros((len(baselines), len(frequencies)), dtype=np.complex128)

        frequencies = frequencies / un.s

        print("Sampling the data onto baselines")

        for i, ff in enumerate(frequencies):
            lamb = const.c / ff.to(1 / un.s)
            u = (baselines[:,0] / lamb).value
            v = (baselines[:,1] / lamb).value

            real = np.real(uvplane[:,:,i])
            imag = np.imag(uvplane[:,:,i])

            f_real = RegularGridInterpolator([uv[0], uv[1]], real)
            f_imag = RegularGridInterpolator([uv[0], uv[1]], imag)

            arr = np.zeros((len(u), 2))
            arr[:, 0] = u
            arr[:, 1] = v

            FT_real = f_real(arr)
            FT_imag = f_imag(arr)

            vis[:, i] = FT_real + FT_imag * 1j

        return vis

    @staticmethod
    def interpolate_frequencies(visibilities, freq_grid, linear_freq):
        """
        Interpolate a set of visibilities from a non-linear grid of frequencies onto a linear grid. Interpolation
        is linear.

        Parameters
        ----------
        visibilities : complex (n_baselines, n_freq)-array
            The visibilities at each baseline and frequency.

        freq_grid : (nfreq)-array
            The grid of frequencies at which the visibilities are defined.

        linear_freq : (N,)-array
            The set of frequencies on which to interpolate the visibilities.

        Returns
        -------
        new_vis : complex (n_baselines, N)-array
            The interpolated visibilities.
        """
        new_vis = np.zeros((visibilities.shape[0], len(linear_freq)), dtype=np.complex64)

        print(freq_grid/1e6)
        print(linear_freq/1e6)
        for i, vis in enumerate(visibilities):
            rl = RegularGridInterpolator((freq_grid[::-1],), np.real(vis)[::-1])(linear_freq)
            im = RegularGridInterpolator((freq_grid[::-1],), np.imag(vis)[::-1])(linear_freq)
            new_vis[i] = rl + im * 1j

        return new_vis

    @staticmethod
    def get_baselines(x, y):
        """
        From a set of antenna positions, determine the non-autocorrelated baselines.

        Parameters
        ----------
        x, y : 1D arrays of the same length.
            The positions of the arrays (presumably in metres).

        Returns
        -------
        baselines : (n_baselines,2)-array
            Each row is the (x,y) co-ordinate of a baseline, in the same units as x,y.
        """
        # ignore up.
        ind = np.tril_indices(len(x), k=-1)
        Xsep = np.add.outer(x, -x)[ind]
        Ysep = np.add.outer(y, -y)[ind]

        # Remove autocorrelations
        zeros = np.logical_and(Xsep.flatten()==0, Ysep.flatten()==0)

        return np.array([Xsep.flatten()[np.logical_not(zeros)], Ysep.flatten()[np.logical_not(zeros)]]).T

    @staticmethod
    def add_thermal_noise(visibilities, frequencies, delta_t = 1200, Tsys=0):
        """
        Add thermal noise to each visibility.

        Parameters
        ----------
        visibilities : (n_baseline, n_freq)-array
            The visibilities at each baseline and frequency.

        frequencies : (n_freq)-array
            The frequencies of the observation.

        delta_t : float, optional
            The integration time

        Returns
        -------

        """
        sigma = Tsys / np.sqrt((frequencies.max()-frequencies.min())*delta_t)
        
        rl = np.random.normal(np.mean(np.real(visibilities)), np.std(np.real(visibilities)))
        im = np.random.normal(np.mean(np.imag(visibilities)), np.std(np.imag(visibilities)))
        
        # TODO: check the units of sigma 
        visibilities += sigma * (rl + im * 1j)
        
        return visibilities