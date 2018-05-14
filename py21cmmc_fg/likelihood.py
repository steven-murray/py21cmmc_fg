#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Fri Apr 20 16:54:22 2018

@author: bella
"""

from powerbox.dft import fft
from powerbox.tools import angular_average_nd
import numpy as np
from astropy import constants as const
from astropy.cosmology import Planck15 as cosmo
from astropy import units as un

from py21cmmc.likelihood import LikelihoodBase, Core21cmFastModule
from cosmoHammer.ChainContext import ChainContext
from cosmoHammer.util import Params


class ForegroundLikelihood(LikelihoodBase):
    """
    This likelihood only works when instrument-like visibilities are in the context (eg. use CoreInstrumentalSampling)
    """

    def __init__(self, datafile, n_uv=None, n_psbins=50, **kwargs):
        super().__init__(**kwargs)
        self.datafile = datafile
        self.n_uv = n_uv
        self.n_psbins = n_psbins

    def setup(self):
        print("read in data")
        data = np.load(self.datafile +".npz")
        
        self.k = data["k"]
        self.power = data["p"]
        self.uncertainty = data["sigma"]

    def computeLikelihood(self, ctx):

        PS_mK2Mpc3, k_Mpc = self.computePower(ctx)

        ## FIND CHI SQUARE OF PS!!!
        # this is a bit too simple. Firstly, you need to make sure that the k values line up. secondly, you need uncertainties.
        return -0.5 * np.sum((self.power - PS_mK2Mpc3) ** 2 / self.uncertainty ** 2)

    def computePower(self, ctx):
        ## Read in data from ctx
        visibilities = ctx.get("visibilities")
        baselines = ctx.get('baselines')
        frequencies = ctx.get("frequencies")
        n_uv = self.n_uv or ctx.get("output").lightcone_box.shape[0]

        ugrid, visgrid, weights = self.grid(visibilities, baselines, frequencies, n_uv)

        ## CONSIDER MOVING INTERPOLATING LINERALY SPACED FREQUENCY HERE INSTEAD

        visgrid, eta = self.frequency_fft(visgrid, frequencies)

        # TODO: this is probably wrong!
        #weights = np.sum(weights, axis=-1)
        power2d, coords  = self.get_2D_power(visgrid, [ugrid, ugrid, eta[0]], weights, frequencies, bins=self.n_psbins )

        # Find the 1D Power Spectrum of the visibility
        #self.get_1D_power(visgrid, [ugrid, ugrid, eta[0]], weights, frequencies, bins=self.n_psbins)
        return power2d, coords
    
    def suppressedFg_1DPower(self, bins = 20):
        
        annuli_bins = np.linspace(0, np.sqrt(self.k[0].max()**2+self.k[1].max()**2), bins)
        
        k_par, k_perp = np.meshgrid(self.k[0], self.k[1])
        
        k_indices = np.digitize(k_par**2+k_perp**2, bins = annuli_bins**2) -1
        
        P_1D = np.zeros(len(annuli_bins))
        uncertainty_1D = np.zeros(len(annuli_bins))

        P_1D[:] = [1 / np.sum(1 / self.uncertainty[k_indices == kk] ** 2) * np.sum((self.power[k_indices == kk] + self.uncertainty[k_indices == kk]) / self.uncertainty[k_indices == kk] ** 2) for kk in range(len(annuli_bins))]
        uncertainty_1D[:] = [np.sum([k_indices == kk]) / np.sum(1 / self.uncertainty[k_indices == kk]) for kk in range(len(annuli_bins))]
        
        return P_1D, uncertainty_1D

    def grid(self, visibilities, baselines, frequencies, ngrid):

        # TODO: may be better to leave this optional for user.
        umax = max([b.max() for b in baselines]) * frequencies.max()/const.c

        ugrid = np.linspace(-umax, umax, ngrid+1) # +1 because these are bin edges.
        visgrid = np.zeros((ngrid, ngrid, len(frequencies)), dtype=np.complex128)
        weights = np.zeros((ngrid, ngrid, len(frequencies)))

        for j, f in enumerate(frequencies):
            # U,V values change with frequency.
            u = baselines[:, 0] * f / const.c
            v = baselines[:, 1] * f / const.c

            # TODO: doing three of the same histograms is probably unnecessary.
            weights[:, :, j] = np.histogram2d(u.value, v.value, bins=[ugrid, ugrid])[0]
            rl = np.histogram2d(u.value, v.value, bins=[ugrid, ugrid], weights=np.real(visibilities[:,j]))[0]
            im = np.histogram2d(u.value, v.value, bins=[ugrid, ugrid], weights=np.imag(visibilities[:,j]))[0]

            visgrid[:, :, j] = (rl + im * 1j) / weights[:, :, j]

        visgrid[np.isnan(visgrid)] = 0.0

        centres = (ugrid[1:] + ugrid[:-1])/2

        return centres, visgrid, weights

    def frequency_fft(self, vis, freq):
        return fft(vis, (freq.max() - freq.min()), axes=(2,), a=0, b=2 * np.pi)

    def get_1D_power(self, visibility, coords, weights, linFrequencies, bins=100):

        print("Finding the power spectrum")
        ## Change the units of coords to Mpc
        z_mid = (1420e6) / (np.mean(linFrequencies)) - 1
        coords[0] = 2 * np.pi * coords[0] / cosmo.comoving_transverse_distance([z_mid])
        coords[1] = 2 * np.pi * coords[1] / cosmo.comoving_transverse_distance([z_mid])
        coords[2] = 2 * np.pi * coords[2] * (cosmo.H0).to(un.m / (un.Mpc * un.s)) * 1420e6 * un.Hz * cosmo.efunc(
            z_mid) / (const.c * (1 + z_mid) ** 2)

        ## Change the unit of visibility
        visibility = visibility / self.convert_factor_sources() * self.convert_factor_HztoMpc(np.min(linFrequencies),
                                                              np.max(linFrequencies)) * self.convert_factor_SrtoMpc2(z_mid)

        ## Square the visibility
        visibility_sq = np.abs(visibility) ** 2

        # TODO: check if this is correct (reshaping might be in wrong order)
        weights = np.repeat(weights, len(coords[2])).reshape((len(coords[0]), len(coords[1]), len(coords[2])))

        PS_mK2Mpc6, k_Mpc = angular_average_nd(visibility_sq, coords, bins=bins, weights=weights)

        PS_mK2Mpc3 = PS_mK2Mpc6 / self.volume(z_mid, np.min(linFrequencies), np.max(linFrequencies))

        return PS_mK2Mpc3, k_Mpc
    
    def get_2D_power(self, V_tilde, coords, weights, linFrequencies, bins=100):
        '''
        Finding the 2D Power Spectrum of the visibility
        
        Parameters
        ----------
            
        V_tilde : float or array-like (complex)
            The visibility
        
        coords: array-like
            The range of values of the (x,y) coordinates in V_tilde
        
        weights: int
            The weighting for the cylindrical averaging
            With the default value, everything has a weight of 1
            When it is set to 0, we only consider cells which are not empty

        bins : int
            The number of radial bin in terms of u and v coordinates of Fourier Transform
        
        Returns
        -------
        P : array-like
            The cylindrical average or 2D Power Spectrum of the visibility, V_tilde
            The shape of P is (number of etas/frequencies)x(number of radial bins)
        '''
        print("Finding the power spectrum")
        
        ## Change the units of coords to Mpc
        z_mid = (1420e6) / (np.mean(linFrequencies)) - 1
        coords[0] = 2 * np.pi * coords[0] / cosmo.comoving_transverse_distance([z_mid])
        coords[1] = 2 * np.pi * coords[1] / cosmo.comoving_transverse_distance([z_mid])
        coords[2] = 2 * np.pi * coords[2] * (cosmo.H0).to(un.m / (un.Mpc * un.s)) * 1420e6 * un.Hz * cosmo.efunc(z_mid) / (const.c * (1 + z_mid) ** 2)
        
        ## Generate the radial bins
        radial_bins = np.linspace(0, np.sqrt(2*np.max(coords[0].value)**2), bins)

        P = np.zeros([np.shape(V_tilde)[-1],len(radial_bins)])
        
        u , v = np.meshgrid(coords[0],coords[1])
        
        bins = np.zeros([np.shape(V_tilde)[-1],len(radial_bins)])
    
        binIndices = np.digitize((u.value**2+v.value**2), bins=radial_bins**2)-1

        V_tilde_sq = np.absolute(V_tilde)**2
                    
        for eta in range(np.shape(V_tilde)[-1]):
    
            P[eta,:] =[np.sum(V_tilde_sq[:,:,eta][binIndices==k]) for k in range(len(radial_bins))]
            
            bins[eta,:] = [np.sum((weights[:,:,eta][binIndices==k])) for k in range(len(radial_bins))]
            
        P[bins>0] = P[bins>0] / bins[bins>0] 
        P = P * (1 / self.convert_factor_sources() * self.convert_factor_HztoMpc(np.min(linFrequencies),
                                                              np.max(linFrequencies)) * self.convert_factor_SrtoMpc2(z_mid))**2/ self.volume(z_mid, np.min(linFrequencies), np.max(linFrequencies))
    
        return P.value, [radial_bins, coords[2].value]
    
    def convert_factor_HztoMpc(self, nu_min, nu_max):

        z_max = (1420e6) / (nu_min) - 1
        z_min = (1420e6) / (nu_max) - 1

        Mpc_Hz = (cosmo.comoving_distance([z_max]) - cosmo.comoving_distance([z_min])) / (nu_max - nu_min)
        
        return Mpc_Hz

    def convert_factor_SrtoMpc2(self, z_mid):

        Mpc2_sr = cosmo.comoving_distance([z_mid]) / (1 * un.sr)

        return Mpc2_sr

    def volume(self, z_mid, nu_min, nu_max, A_eff=20):

        diff_nu = nu_max - nu_min

        G_z = (cosmo.H0).to(un.m / (un.Mpc * un.s)) * 1420e6 * un.Hz * cosmo.efunc(z_mid) / (const.c * (1 + z_mid) ** 2)

        Vol = const.c ** 2 / (A_eff * un.m ** 2 * nu_max * (1 / un.s) ** 2) * diff_nu * (
                    1 / un.s) * cosmo.comoving_distance([z_mid]) ** 2 / (G_z)

        return Vol.value

    def convert_factor_sources(self, nu=0):

        ## Can either do it with the beam or without the beam (frequency dependent)
        if (nu == 0):
            A_eff = 20 * un.m ** 2

            flux_density = (2 * 1e26 * const.k_B * 1e-3 * un.K / (A_eff * (1 * un.Hz) * (1 * un.s))).to(
                un.W / (un.Hz * un.m ** 2))

        else:
            flux_density = (2 * const.k_B * 1e-3 * un.K / (((const.c) / (nu.to(1 / un.s))) ** 2) * 1e26).to(
                un.W / (un.Hz * un.m ** 2))
        
        return flux_density.value

    def simulate_data(self, fg_core, instr_core, params, niter=20):
        core = Core21cmFastModule(
            parameter_names=params.keys(),
            box_dim=self._box_dim,
            flag_options=self._flag_options,
            astro_params=self._astro_params,
            cosmo_params=self._cosmo_params
        )

        core.setup()
        fg_core.setup()
        instr_core.setup()

        params = Params(*[(k, v[1]) for k, v in params.items()])
        ctx = ChainContext('derp', params)

        p = [0] * niter
        for i in range(niter):
            # Here is where the __call__ happens!
            core(ctx)
            fg_core(ctx)
            instr_core(ctx)

            p[i], k = self.computePower(ctx)

        sigma = np.std(np.array(p), axis=0)
        p = np.mean(np.array(p), axis=0)

        np.savez(self.datafile, k=k, p=p, sigma=sigma)